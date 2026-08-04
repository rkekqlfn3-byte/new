import copy
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

from engine.execution_result import normalize_error_type
from engine.learning_quality import (
    LEARNED_STATES,
    apply_execution_result,
    clean_trigger_list,
    ensure_quality_fields,
    find_trigger_conflicts,
)
from engine.learning_schema import literal_utterances, normalize_learning_metadata
from engine.managers.app_scanner import (
    is_noise_app_candidate,
    scan_matching_windows_apps,
    scan_recent_windows_apps,
    scan_windows_apps,
)
from engine.managers.browser_scanner import scan_chrome_bookmarks
from engine.managers.config_manager import ConfigManager
from engine.managers.macro_manager import MacroManager
from engine.runtime_paths import user_data_path
from engine.security.credential_protection import (
    CredentialProtectionError,
    default_credential_protector,
)
from engine.security.launch_policy import is_safe_launch_target
from engine.skills.run_policy import (
    SkillRunPolicyService,
    change_run_policy,
    ensure_run_policy_fields,
)
from engine.storage.json_store import atomic_write_json, safe_read_json

DICTIONARY_PATH = user_data_path("dictionaries.json")
DICTIONARY_SCHEMA_VERSION = 5


def _manager_locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def migrate_v1_to_v2(data):
    migrated = dict(data)
    migrated["schema_version"] = 2
    return migrated


def migrate_v2_to_v3(data):
    migrated = dict(data)
    learned_macros = migrated.get("learned_macros", {})
    if isinstance(learned_macros, dict):
        for app_macros in learned_macros.values():
            if not isinstance(app_macros, dict):
                continue
            for macro_name, record in app_macros.items():
                ensure_quality_fields(record, macro_name)
    migrated["schema_version"] = 3
    return migrated


def migrate_v3_to_v4(data):
    migrated = dict(data)
    learned_macros = migrated.get("learned_macros", {})
    if isinstance(learned_macros, dict):
        for app_macros in learned_macros.values():
            if not isinstance(app_macros, dict):
                continue
            for record in app_macros.values():
                ensure_run_policy_fields(record, changed_by="migration")
    migrated["schema_version"] = 4
    return migrated


def migrate_v4_to_v5(data, credential_protector):
    migrated = dict(data)
    ai_config = dict(migrated.get("ai_config", {}))
    legacy_api_key = str(ai_config.pop("api_key", "") or "").strip()
    if legacy_api_key:
        ai_config["api_key_protected"] = credential_protector.protect(
            legacy_api_key
        )
    else:
        ai_config.setdefault("api_key_protected", "")
    migrated["ai_config"] = ai_config
    migrated["schema_version"] = 5
    return migrated


DICTIONARY_MIGRATIONS = {
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
    3: migrate_v3_to_v4,
}


def migrate_dictionary_data(data, credential_protector=None):
    migrated = dict(data) if isinstance(data, dict) else {}
    protector = credential_protector or default_credential_protector()
    original_config = migrated.get("ai_config", {})
    credential_migrated = bool(
        isinstance(original_config, dict)
        and str(original_config.get("api_key", "") or "").strip()
    )
    try:
        version = int(migrated.get("schema_version", 1))
    except (TypeError, ValueError):
        version = 1
    changed = False
    while version < DICTIONARY_SCHEMA_VERSION:
        migration = (
            (lambda value: migrate_v4_to_v5(value, protector))
            if version == 4 else DICTIONARY_MIGRATIONS.get(version)
        )
        if migration is None:
            raise ValueError(f"사전 스키마 {version} 변환 함수를 찾지 못했습니다.")
        migrated = migration(migrated)
        version += 1
        changed = True
    stored_config = migrated.get("ai_config", {})
    if isinstance(stored_config, dict) and "api_key" in stored_config:
        migrated = migrate_v4_to_v5(migrated, protector)
        changed = True
    return migrated, changed, credential_migrated

class DictionaryManager:
    def __init__(self, dictionary_path=None, credential_protector=None):
        self._lock = threading.RLock()
        self.dictionary_path = dictionary_path or DICTIONARY_PATH
        self.credential_protector = (
            credential_protector or default_credential_protector()
        )
        self._protected_api_key = ""
        self.credential_error = ""
        self.noun_dict = {}
        self.noun_revision = 0
        self.macro_dict = {}
        self.search_engines_dict = {}
        self.favorites = []
        self.user_nouns = set()
        self.has_scanned = False
        self.ai_config = {
            "provider": "openai", "api_key": "", "routing_mode": "auto",
        }
        self.learned_macros = {}
        self.load()
        
        # Sub-managers
        self.macro_manager = MacroManager(self.macro_dict, self.save, self._lock)
        self.config_manager = ConfigManager(
            self.ai_config,
            self.save,
            self._lock,
            credential_protector=self.credential_protector,
            protected_api_key=self._protected_api_key,
            credential_error=self.credential_error,
        )

    @contextmanager
    def locked(self):
        """Hold one mutation transaction across memory changes and save()."""
        with self._lock:
            yield self

    @_manager_locked
    def _touch_nouns(self):
        self.noun_revision += 1

    @_manager_locked
    def _repair_learned_macro_links(self):
        """Connect legacy learned code to the local action dictionary."""
        changed = False
        if not isinstance(self.learned_macros, dict):
            self.learned_macros = {}
            return True

        taken_names = set(self.macro_dict)
        for app_name, app_macros in list(self.learned_macros.items()):
            if not isinstance(app_macros, dict):
                continue
            for old_name, learned in list(app_macros.items()):
                if not isinstance(learned, dict) or not (
                    str(learned.get("code", "")).strip()
                    or isinstance(learned.get("plan"), list) and learned.get("plan")
                ):
                    continue

                macro_name = str(old_name or "").strip()
                if not macro_name:
                    base = "learned_macro"
                    suffix = 1
                    macro_name = base
                    while macro_name in taken_names or macro_name in app_macros:
                        suffix += 1
                        macro_name = f"{base}_{suffix}"
                    del app_macros[old_name]
                    app_macros[macro_name] = learned
                    changed = True

                existing = self.macro_dict.get(macro_name)
                if existing and (
                    existing.get("type") != "learned"
                    or existing.get("app") not in {None, app_name}
                ):
                    base = macro_name
                    suffix = 2
                    while f"{base}_{suffix}" in taken_names or f"{base}_{suffix}" in app_macros:
                        suffix += 1
                    new_name = f"{base}_{suffix}"
                    if new_name != macro_name:
                        del app_macros[macro_name]
                        app_macros[new_name] = learned
                        macro_name = new_name
                        changed = True

                raw_utterances = learned.get("utterances", [])
                learning = learned.get("learning", {})
                if isinstance(learning, dict) and learning.get("utterances"):
                    raw_utterances = learning.get("utterances", [])
                utterances = clean_trigger_list(raw_utterances, macro_name)
                if ensure_quality_fields(learned, macro_name):
                    changed = True
                if ensure_run_policy_fields(learned, changed_by="migration"):
                    changed = True

                entry = self.macro_dict.setdefault(macro_name, {})
                safe_synonyms = literal_utterances({"utterances": utterances})
                if entry.get("synonyms") != safe_synonyms:
                    entry["synonyms"] = safe_synonyms
                    changed = True
                expected = {
                    "name": macro_name,
                    "description": f"[{app_name}] {learned.get('description', '학습된 매크로')}",
                    "type": "learned",
                    "app": app_name,
                    "state": learned.get("state", "needs_review"),
                }
                if isinstance(learning, dict):
                    expected.update({
                        "intent": learning.get("intent", "LEARNED_ACTION"),
                        "verbs": learning.get("verbs", []),
                        "slots": learning.get("slots", []),
                    })
                for key, value in expected.items():
                    if entry.get(key) != value:
                        entry[key] = value
                        changed = True
                taken_names.add(macro_name)
        return changed

    @_manager_locked
    def load(self):
        file_existed = os.path.exists(self.dictionary_path)
        data = safe_read_json(self.dictionary_path, {}) if file_existed else {}
        if not isinstance(data, dict):
            data = {}
        original_data = copy.deepcopy(data)
        try:
            data, migrated, credential_migrated = migrate_dictionary_data(
                data, self.credential_protector
            )
        except CredentialProtectionError:
            data = original_data
            migrated = False
            credential_migrated = False
            self.credential_error = "credential_protection_unavailable"
        storage_changed = not file_existed or migrated

        if data:
            self.noun_dict = data.get("noun_dictionary", {})
            self.macro_dict = data.get("macro_dictionary", {})
            self.search_engines_dict = data.get("search_engines_dict", {})
            self.favorites = data.get("favorites", [])
            self.user_nouns = set(data.get("user_nouns", []))
            self.has_scanned = data.get("has_scanned", False)
            loaded_ai_config = data.get("ai_config", {})
            provider = str(loaded_ai_config.get("provider", "openai")).casefold()
            routing_mode = str(
                loaded_ai_config.get("routing_mode", "auto")
            ).casefold()
            if provider not in ConfigManager.PROVIDERS:
                provider = "openai"
                storage_changed = True
            if routing_mode not in ConfigManager.ROUTING_MODES:
                routing_mode = "auto"
                storage_changed = True
            if set(loaded_ai_config) - {
                "provider", "api_key_protected", "routing_mode"
            }:
                storage_changed = True
            protected_api_key = str(
                loaded_ai_config.get("api_key_protected", "") or ""
            )
            api_key = ""
            if protected_api_key and not self.credential_error:
                try:
                    api_key = self.credential_protector.unprotect(
                        protected_api_key
                    )
                except CredentialProtectionError:
                    self.credential_error = "credential_read_failed"
            elif loaded_ai_config.get("api_key"):
                self.credential_error = "credential_migration_required"
            self._protected_api_key = protected_api_key
            self.ai_config = {
                "provider": provider,
                "api_key": api_key,
                "routing_mode": routing_mode,
            }
            self.learned_macros = data.get("learned_macros", {})

            # load() can also be called after startup. Keep sub-managers
            # connected to the newly loaded dictionaries/config object.
            if hasattr(self, "macro_manager"):
                self.macro_manager.macro_dict = self.macro_dict
            if hasattr(self, "config_manager"):
                self.config_manager.replace_loaded_config(
                    self.ai_config,
                    self._protected_api_key,
                    self.credential_error,
                )
                
        # Inject default search engines if not present
        default_search = {
            "구글": "https://www.google.com/search?q=",
            "네이버": "https://search.naver.com/search.naver?query=",
            "유튜브": "https://www.youtube.com/results?search_query=",
            "나무위키": "https://namu.wiki/w/"
        }
        for engine, url in default_search.items():
            if engine not in self.search_engines_dict:
                self.search_engines_dict[engine] = url

        # Inject default macros if empty
        default_defs = {
            "OPEN": {"description": "지정된 앱이나 웹사이트를 실행합니다.", "synonyms": ["열어", "켜", "실행", "틀어", "시작", "띄워"]},
            "CLOSE": {"description": "현재 활성화된 창을 닫습니다.", "synonyms": ["닫아", "꺼", "종료", "그만", "중지"]},
            "SEARCH": {"description": "지정된 검색 엔진에서 검색을 수행합니다.", "synonyms": ["검색", "찾아", "알아봐", "쳐"]},
            "PLAYPAUSE": {"description": "미디어를 재생하거나 일시 정지합니다.", "synonyms": ["재생", "일시정지", "멈춰", "계속"]},
            "VOL_UP": {"description": "시스템 볼륨을 높입니다.", "synonyms": ["소리 키워", "볼륨 키워", "볼륨 업", "볼륨 올려", "볼륨 높여", "소리 올려", "크게"]},
            "VOL_DOWN": {"description": "시스템 볼륨을 낮춥니다.", "synonyms": ["소리 줄여", "볼륨 줄여", "볼륨 낮춰", "볼륨 다운", "볼륨 내려", "소리 내려", "작게", "좀 줄여"]},
            "VOL_SET": {"description": "시스템 볼륨을 지정한 퍼센트로 설정합니다.", "synonyms": ["볼륨 설정", "소리 설정"]},
            "MUTE": {"description": "시스템 소리를 음소거하거나 해제합니다.", "synonyms": ["음소거 해제", "음소거 풀어", "소리 다시 켜", "소리 켜", "음소거", "조용히", "소리 꺼"]},
            "SHUTDOWN": {"description": "컴퓨터를 60초 후 종료합니다.", "synonyms": ["컴퓨터 꺼", "시스템 종료", "셧다운"]},
            "CANCEL_SHUTDOWN": {"description": "예약된 시스템 종료를 취소합니다.", "synonyms": ["취소해", "종료 취소"]},
            "TIME": {"description": "현재 시간을 알려줍니다.", "synonyms": ["몇 시", "시간 알려줘", "지금 시간", "시간이"]},
            "DATE": {"description": "현재 날짜와 요일을 알려줍니다.", "synonyms": ["오늘 날짜", "현재 날짜", "지금 날짜", "오늘 며칠", "오늘 몇일", "무슨 요일"]},
            "WEATHER": {"description": "기본 브라우저로 날씨를 검색합니다.", "synonyms": ["날씨", "오늘 날씨"]}
        }
        
        if not self.macro_dict:
            self.macro_dict = default_defs
        else:
            for k, v in default_defs.items():
                if k in self.macro_dict:
                    if "description" not in self.macro_dict[k]:
                        self.macro_dict[k]["description"] = v["description"]
                    
                    existing_synonyms = set(self.macro_dict[k].setdefault("synonyms", []))
                    for syn in v["synonyms"]:
                        if syn not in existing_synonyms:
                            self.macro_dict[k]["synonyms"].append(syn)
                else:
                    self.macro_dict[k] = v

        if self._repair_learned_macro_links():
            storage_changed = True

        if storage_changed and not self.credential_error:
            self.save(backup_existing=not credential_migrated)
        if not self.credential_error:
            self._migrate_credential_backups()

        self._touch_nouns()

    @_manager_locked
    def save(self, *, backup_existing=True):
        if hasattr(self, "config_manager"):
            stored_ai_config = self.config_manager.serialized_config()
        else:
            stored_ai_config = {
                "provider": self.ai_config.get("provider", "openai"),
                "api_key_protected": self._protected_api_key,
                "routing_mode": self.ai_config.get("routing_mode", "auto"),
            }
        data = copy.deepcopy({
            "schema_version": DICTIONARY_SCHEMA_VERSION,
            "has_scanned": self.has_scanned,
            "noun_dictionary": self.noun_dict,
            "macro_dictionary": self.macro_dict,
            "search_engines_dict": self.search_engines_dict,
            "favorites": self.favorites,
            "user_nouns": sorted(self.user_nouns),
            "ai_config": stored_ai_config,
            "learned_macros": self.learned_macros
        })
        directory = os.path.dirname(os.path.abspath(self.dictionary_path))
        atomic_write_json(
            self.dictionary_path,
            data,
            max_versions=5,
            backup_dir=os.path.join(directory, "backups"),
            version_interval_seconds=300,
            backup_existing=backup_existing,
        )

    def _migrate_credential_backups(self):
        path = os.path.abspath(self.dictionary_path)
        directory = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        candidates = [f"{path}.bak"]
        backup_dir = os.path.join(directory, "backups")
        if os.path.isdir(backup_dir):
            candidates.extend(
                os.path.join(backup_dir, name)
                for name in os.listdir(backup_dir)
                if name.startswith(f"{stem}_") and name.endswith(".json")
            )
        for candidate in candidates:
            if not os.path.isfile(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as source:
                    data = json.load(source)
                migrated, changed, _ = migrate_dictionary_data(
                    data, self.credential_protector
                )
                if changed:
                    atomic_write_json(
                        candidate, migrated, backup_existing=False
                    )
            except (OSError, UnicodeError, ValueError, CredentialProtectionError):
                self.credential_error = "credential_backup_migration_failed"
                if hasattr(self, "config_manager"):
                    self.config_manager.credential_error = self.credential_error
                break

    @_manager_locked
    def get_learned_macro_records(self):
        """Return editable learning metadata without exposing executable code."""
        records = []
        for app_name, app_macros in self.learned_macros.items():
            if not isinstance(app_macros, dict):
                continue
            for macro_name, learned in app_macros.items():
                if not isinstance(learned, dict):
                    continue
                learning = learned.get("learning", {})
                plan = learned.get("plan", [])
                records.append({
                    "app": app_name,
                    "name": macro_name,
                    "description": learned.get("description", "학습된 매크로"),
                    "kind": "action_plan" if plan else "dynamic_code",
                    "step_count": len(plan) if isinstance(plan, list) else 0,
                    "intent": learning.get("intent", "LEARNED_ACTION"),
                    "verbs": list(learning.get("verbs", [])),
                    "utterances": list(learning.get("utterances", [])),
                    "nouns": list(learning.get("nouns", [])),
                    "slots": list(learning.get("slots", [])),
                    "verification_status": learned.get(
                        "verification_status", "unknown"
                    ),
                    "state": learned.get("state", "needs_review"),
                    "state_reason": learned.get("state_reason", ""),
                    "consecutive_failures": int(
                        learned.get("consecutive_failures", 0) or 0
                    ),
                    "last_failure_type": learned.get("last_failure_type", ""),
                    "usage_count": int(learned.get("usage_count", 0) or 0),
                    "success_count": int(learned.get("success_count", 0) or 0),
                    "failure_count": int(learned.get("failure_count", 0) or 0),
                    "last_used_at": learned.get("last_used_at", ""),
                    "last_status": learned.get("last_status", "never"),
                    "run_policy": learned.get("run_policy", "confirm"),
                    "run_policy_history": copy.deepcopy(
                        learned.get("run_policy_history", [])
                    ),
                    "verified_success_count": int(
                        learned.get("verified_success_count", 0) or 0
                    ),
                    "consecutive_verified_success": int(
                        learned.get("consecutive_verified_success", 0) or 0
                    ),
                })
        return sorted(
            records,
            key=lambda item: (
                -item["usage_count"], item["app"].lower(), item["name"].lower()
            ),
        )

    @staticmethod
    def _clean_learned_macro_name(value):
        return re.sub(
            r"[^0-9a-zA-Z가-힣_-]+", "_", str(value or "").strip()
        ).strip("_-")[:100]

    @_manager_locked
    def update_learned_macro(self, app_name, macro_name, edits):
        app_macros = self.learned_macros.get(app_name)
        if not isinstance(app_macros, dict) or macro_name not in app_macros:
            raise ValueError("수정할 학습 매크로를 찾지 못했습니다.")
        if not isinstance(edits, dict):
            raise ValueError("학습 수정값이 올바르지 않습니다.")

        new_name = self._clean_learned_macro_name(edits.get("name"))
        utterances = edits.get("utterances")
        verbs = edits.get("verbs")
        if not new_name:
            raise ValueError("매크로 이름을 입력하세요.")
        if new_name != macro_name and (
            new_name in self.macro_dict or new_name in app_macros
        ):
            raise ValueError(f"'{new_name}' 이름이 이미 사용 중입니다.")
        if not isinstance(utterances, list) or not any(
            isinstance(value, str) and value.strip() for value in utterances
        ):
            raise ValueError("발동 문장을 하나 이상 입력하세요.")
        if not isinstance(verbs, list):
            raise ValueError("핵심 동사 값이 올바르지 않습니다.")

        learned = dict(app_macros[macro_name])
        raw_learning = dict(learned.get("learning", {}))
        raw_learning["utterances"] = utterances
        raw_learning["verbs"] = verbs
        learning = normalize_learning_metadata({"learning": raw_learning})
        safe_utterances = clean_trigger_list(learning.get("utterances", []), new_name)
        if not safe_utterances:
            raise ValueError("내부 이름이 아닌 자연스러운 발동 문장을 하나 이상 입력하세요.")
        conflicts = find_trigger_conflicts(
            self.learned_macros, safe_utterances, exclude=(app_name, macro_name)
        )
        if conflicts:
            conflict = conflicts[0]
            raise ValueError(
                f"발동 문장 '{conflict['utterance']}'이 "
                f"[{conflict['app']}/{conflict['macro']}]와 충돌합니다."
            )
        learning["utterances"] = safe_utterances
        literal_phrases = literal_utterances(learning)
        learned["learning"] = learning
        learned["utterances"] = literal_phrases
        learned["state"] = str(edits.get("state", learned.get("state", "active")))
        if learned["state"] not in LEARNED_STATES:
            raise ValueError("학습 상태 값이 올바르지 않습니다.")
        learned["state_reason"] = ""
        requested_policy = str(
            edits.get("run_policy", learned.get("run_policy", "confirm"))
        ).strip().casefold()
        if requested_policy == "auto" and learned.get("run_policy") != "auto":
            allowed, assessment = SkillRunPolicyService().can_enable_auto(learned)
            if not allowed:
                reasons = ", ".join(assessment.forced_reasons) or (
                    f"자동 검증 연속 성공 {assessment.consecutive_verified_success}/5"
                )
                raise ValueError(
                    "이 스킬은 아직 자동 실행으로 변경할 수 없습니다: " + reasons
                )
        change_run_policy(
            learned,
            requested_policy,
            changed_by="user",
            reason="행동 사전에서 사용자가 실행 정책을 변경",
        )

        old_entry = self.macro_dict.get(macro_name, {})
        if new_name != macro_name:
            del app_macros[macro_name]
            if old_entry.get("type") == "learned" and old_entry.get("app") == app_name:
                self.macro_dict.pop(macro_name, None)
        app_macros[new_name] = learned
        self.macro_dict[new_name] = {
            **old_entry,
            "name": new_name,
            "description": f"[{app_name}] {learned.get('description', '학습된 매크로')}",
            "type": "learned",
            "app": app_name,
            "synonyms": literal_phrases,
            "intent": learning.get("intent", "LEARNED_ACTION"),
            "verbs": learning.get("verbs", []),
            "slots": learning.get("slots", []),
            "state": learned["state"],
        }
        self.save()
        return {"app": app_name, "name": new_name}

    @_manager_locked
    def delete_learned_macro(self, app_name, macro_name):
        app_macros = self.learned_macros.get(app_name)
        if not isinstance(app_macros, dict) or macro_name not in app_macros:
            return False
        del app_macros[macro_name]
        if not app_macros:
            self.learned_macros.pop(app_name, None)
        entry = self.macro_dict.get(macro_name, {})
        if entry.get("type") == "learned" and entry.get("app") == app_name:
            self.macro_dict.pop(macro_name, None)
        self.save()
        return True

    @_manager_locked
    def set_learned_macro_state(self, app_name, macro_name, state):
        if state not in LEARNED_STATES:
            raise ValueError("학습 상태 값이 올바르지 않습니다.")
        app_macros = self.learned_macros.get(app_name)
        learned = app_macros.get(macro_name) if isinstance(app_macros, dict) else None
        if not isinstance(learned, dict):
            raise ValueError("상태를 변경할 학습 매크로를 찾지 못했습니다.")
        if state == "active":
            learning = learned.get("learning", {})
            utterances = (
                learning.get("utterances", [])
                if isinstance(learning, dict)
                else []
            ) or learned.get("utterances", [])
            safe_utterances = clean_trigger_list(utterances, macro_name)
            if not safe_utterances:
                raise ValueError(
                    "활성화하려면 내부 이름이 아닌 자연스러운 발동 문장을 "
                    "하나 이상 입력하세요."
                )
            conflicts = find_trigger_conflicts(
                self.learned_macros,
                safe_utterances,
                exclude=(app_name, macro_name),
            )
            if conflicts:
                conflict = conflicts[0]
                raise ValueError(
                    f"발동 문장 '{conflict['utterance']}'이 "
                    f"[{conflict['app']}/{conflict['macro']}]와 충돌합니다."
                )
        learned["state"] = state
        learned["state_reason"] = "사용자가 상태를 변경했습니다."
        entry = self.macro_dict.get(macro_name)
        if isinstance(entry, dict) and entry.get("app") == app_name:
            entry["state"] = state
        self.save()
        return {"app": app_name, "name": macro_name, "state": state}

    @_manager_locked
    def set_learned_macro_run_policy(
        self,
        app_name,
        macro_name,
        policy,
        *,
        changed_by="user",
        reason="",
    ):
        app_macros = self.learned_macros.get(app_name)
        learned = app_macros.get(macro_name) if isinstance(app_macros, dict) else None
        if not isinstance(learned, dict):
            raise ValueError("실행 정책을 변경할 학습 매크로를 찾지 못했습니다.")
        normalized = str(policy or "").strip().casefold()
        if normalized == "auto":
            allowed, assessment = SkillRunPolicyService().can_enable_auto(learned)
            if not allowed:
                reasons = ", ".join(assessment.forced_reasons) or (
                    f"자동 검증 연속 성공 {assessment.consecutive_verified_success}/5"
                )
                raise ValueError(
                    "이 스킬은 아직 자동 실행으로 변경할 수 없습니다: " + reasons
                )
        changed = change_run_policy(
            learned,
            normalized,
            changed_by=changed_by,
            reason=reason,
        )
        if changed:
            self.save()
        return {
            "app": app_name,
            "name": macro_name,
            "run_policy": learned["run_policy"],
            "changed": changed,
            "history": copy.deepcopy(learned.get("run_policy_history", [])),
        }

    @_manager_locked
    def record_learned_macro_result(
        self, app_name, macro_name, success, failure_type="unknown"
    ):
        app_macros = self.learned_macros.get(app_name)
        if not isinstance(app_macros, dict):
            return False
        learned = app_macros.get(macro_name)
        if not isinstance(learned, dict):
            return False
        failure_type = normalize_error_type(failure_type)
        if failure_type != "user_cancelled":
            learned["usage_count"] = int(learned.get("usage_count", 0) or 0) + 1
        counter = "success_count" if success else "failure_count"
        if failure_type != "user_cancelled":
            learned[counter] = int(learned.get(counter, 0) or 0) + 1
        if not success and failure_type != "user_cancelled":
            learned["consecutive_verified_success"] = 0
        learned["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        learned["last_status"] = "success" if success else (
            "cancelled" if failure_type == "user_cancelled" else "failed"
        )
        state = apply_execution_result(learned, success, failure_type)
        entry = self.macro_dict.get(macro_name)
        if isinstance(entry, dict) and entry.get("app") == app_name:
            entry["state"] = state
        self.save()
        return True

    @_manager_locked
    def record_learned_macro_verification(
        self, app_name, macro_name, verified
    ):
        app_macros = self.learned_macros.get(app_name)
        learned = app_macros.get(macro_name) if isinstance(app_macros, dict) else None
        if not isinstance(learned, dict):
            return False
        ensure_run_policy_fields(learned, changed_by="migration")
        if bool(verified):
            learned["verified_success_count"] = int(
                learned.get("verified_success_count", 0) or 0
            ) + 1
            learned["consecutive_verified_success"] = int(
                learned.get("consecutive_verified_success", 0) or 0
            ) + 1
        else:
            learned["consecutive_verified_success"] = 0
        self.save()
        return True

    # --- Scanning Delegates ---
    @_manager_locked
    def prune_invalid_nouns(self, remove_noise=True, save=True):
        """Remove unusable scanned entries while preserving user favorites."""
        removed = {"missing": [], "noise": [], "unsafe": [], "normalized": []}
        protected_nouns = set(self.favorites) | self.user_nouns

        for noun, raw_path in list(self.noun_dict.items()):
            if not isinstance(raw_path, str):
                continue
            path = raw_path.strip().strip('"')
            if path.startswith(("http://", "https://")):
                continue

            expanded_path = os.path.expandvars(os.path.expanduser(path))
            reason = None
            if not is_safe_launch_target(expanded_path, noun):
                # Security policy also applies to favorites and manually added
                # nouns; otherwise a persisted console tool could bypass OPEN.
                reason = "unsafe"
            elif noun in protected_nouns:
                continue
            elif os.path.isabs(expanded_path) and not os.path.exists(expanded_path):
                reason = "missing"
            elif remove_noise and is_noise_app_candidate(noun, expanded_path):
                reason = "noise"

            if reason:
                removed[reason].append({"noun": noun, "path": raw_path})
                del self.noun_dict[noun]
            elif path != raw_path and os.path.isabs(expanded_path):
                self.noun_dict[noun] = path
                removed["normalized"].append({"noun": noun, "path": raw_path})

        if any(removed.values()):
            self._touch_nouns()
            if save:
                self.save()
        return removed

    @_manager_locked
    def scan_apps(self, force=False):
        removed = self.prune_invalid_nouns(remove_noise=True, save=False)
        if self.has_scanned and not force:
            if any(removed.values()):
                self.save()
            return -1
        apps_found = scan_windows_apps(self.noun_dict)
        if apps_found > 0:
            self._touch_nouns()
        self.has_scanned = True
        self.save()
        return apps_found

    @_manager_locked
    def scan_recent_apps(self, hours=24):
        removed = self.prune_invalid_nouns(remove_noise=True, save=False)
        apps_found = scan_recent_windows_apps(self.noun_dict, hours)
        if apps_found > 0 or any(removed.values()):
            self._touch_nouns()
            self.save()
        return apps_found

    @_manager_locked
    def scan_web_bookmarks(self):
        apps_found = scan_chrome_bookmarks(self.noun_dict)
        if apps_found > 0:
            self._touch_nouns()
            self.save()
        return apps_found

    @_manager_locked
    def discover_apps(self, candidates):
        """Run one bounded, target-specific app rediscovery pass."""
        apps_found = scan_matching_windows_apps(self.noun_dict, candidates)
        if apps_found > 0:
            self._touch_nouns()
            self.save()
        return apps_found

    # --- Noun Dictionary Methods ---
    @_manager_locked
    def get_nouns(self):
        return dict(self.noun_dict)

    @_manager_locked
    def add_noun_synonym(self, original_noun, synonym):
        if original_noun in self.noun_dict:
            synonym = synonym.lower()
            self.noun_dict[synonym] = self.noun_dict[original_noun]
            self.user_nouns.add(synonym)
            self._touch_nouns()
            self.save()
            return True
        return False

    @_manager_locked
    def remove_noun_synonym(self, synonym):
        syn = synonym.lower()
        if syn in self.noun_dict:
            del self.noun_dict[syn]
            self.user_nouns.discard(syn)
            self._touch_nouns()
            self.save()
            return True
        return False
        
    @_manager_locked
    def add_custom_noun(self, noun, path):
        noun = noun.lower()
        self.noun_dict[noun] = path
        self.user_nouns.add(noun)
        self._touch_nouns()
        self.save()
        return True

    @_manager_locked
    def add_verified_noun_aliases(self, nouns, save=True):
        """Link AI-proposed aliases only to an already verified dictionary target."""
        if not isinstance(nouns, list):
            return []
        added = []
        for item in nouns:
            if not isinstance(item, dict) or item.get("type") not in {"app", "website"}:
                continue
            alias = str(item.get("text", "")).strip().lower()
            canonical = str(item.get("canonical", "")).strip().lower()
            if not alias or not canonical or canonical not in self.noun_dict:
                continue
            if alias in self.noun_dict:
                continue
            self.noun_dict[alias] = self.noun_dict[canonical]
            self.user_nouns.add(alias)
            added.append(alias)
        if added:
            self._touch_nouns()
            if save:
                self.save()
        return added
        
    @_manager_locked
    def rename_noun(self, old_noun, new_noun):
        old = old_noun.lower()
        new = new_noun.lower()
        if old in self.noun_dict and new and new not in self.noun_dict:
            path = self.noun_dict.pop(old)
            self.noun_dict[new] = path
            if old in self.user_nouns:
                self.user_nouns.remove(old)
            self.user_nouns.add(new)
            if old in self.favorites:
                self.favorites.remove(old)
                self.favorites.append(new)
            self._touch_nouns()
            self.save()
            return True
        return False
        
    @_manager_locked
    def get_synonyms_for_path(self, path, exclude_noun=None):
        syns = []
        for s, p in self.noun_dict.items():
            if p == path and s != exclude_noun:
                syns.append(s)
        return syns

    @_manager_locked
    def toggle_favorite(self, noun):
        if noun in self.favorites:
            self.favorites.remove(noun)
        else:
            self.favorites.append(noun)
        self.save()
        return noun in self.favorites

    @_manager_locked
    def get_favorites(self):
        return list(self.favorites)

    @_manager_locked
    def ai_find_exe(self, query):
        import os
        candidates = []
        for n, p in self.noun_dict.items():
            if p and not p.startswith("http"):
                filename = os.path.basename(p).lower()
                if query.lower() in filename or query.lower() in n:
                    candidates.append({"name": n, "path": p})
        
        unique_candidates = {c["path"]: c["name"] for c in candidates}
        results = [{"name": v, "path": k} for k, v in unique_candidates.items()][:5]
        return results

    # --- Config Delegate Methods ---
    @_manager_locked
    def get_ai_config(self):
        return self.config_manager.get_ai_config()

    @_manager_locked
    def save_ai_config(self, provider, api_key, routing_mode="auto"):
        return self.config_manager.save_ai_config(provider, api_key, routing_mode)
