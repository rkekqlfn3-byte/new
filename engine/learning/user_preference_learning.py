"""Evidence-based, user-approved preference learning for Stage 11."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json


LEARNING_SCHEMA_VERSION = 1
DEFAULT_LEARNING_PATH = user_data_path("user_style_preferences.json")
SCOPE_KINDS = frozenset({"global", "app", "workflow", "file"})
APP_IDS = frozenset({"excel", "hwp", "word", "powerpoint"})
WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")

ALLOWED_PREFERENCES = {
    "summary_lines": {"kind": "int", "minimum": 1, "maximum": 20},
    "report_tone": {
        "kind": "enum",
        "values": ("formal", "concise", "friendly"),
    },
    "title_style": {
        "kind": "enum",
        "values": ("default", "short", "noun", "sentence"),
    },
    "number_format": {
        "kind": "enum",
        "values": ("plain", "thousands", "currency_krw", "percent"),
    },
    "table_style": {
        "kind": "enum",
        "values": ("basic", "header_bold", "banded"),
    },
    "ppt_slide_count": {"kind": "int", "minimum": 3, "maximum": 20},
    "preferred_output_dir": {"kind": "absolute_directory"},
    "confirmation_actions": {
        "kind": "string_list",
        "values": (
            "document_creation",
            "document_edit",
            "vba_edit",
            "vba_run",
        ),
    },
    "workflow_order": {
        "kind": "string_list",
        "values": (
            "analyze_excel",
            "create_word_report",
            "create_powerpoint_summary",
        ),
    },
    "vba_edit_pattern": {
        "kind": "enum",
        "values": (
            "backup_module",
            "backup_active_sheet",
            "selection_only",
            "fix_last_row",
        ),
    },
    "emphasis_style": {
        "kind": "enum",
        "values": ("bold", "regular"),
    },
    "font_scale": {
        "kind": "enum",
        "values": ("larger", "smaller"),
    },
    "paragraph_align": {
        "kind": "enum",
        "values": ("left", "center", "right", "justify"),
    },
}


class UserPreferenceLearningError(ValueError):
    pass


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_id(scope_kind: str, scope_id: str, preference: str) -> str:
    encoded = f"{scope_kind}\0{scope_id}\0{preference}".encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _validate_preference_name(value) -> str:
    name = str(value or "").strip().casefold()
    if name not in ALLOWED_PREFERENCES:
        raise UserPreferenceLearningError(f"지원하지 않는 사용자 선호 항목입니다: {value}")
    return name


def _validate_value(name: str, value):
    spec = ALLOWED_PREFERENCES[name]
    kind = spec["kind"]
    if kind == "int":
        if isinstance(value, bool):
            raise UserPreferenceLearningError(f"{name} 값은 숫자여야 합니다.")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise UserPreferenceLearningError(f"{name} 값은 숫자여야 합니다.") from error
        if not int(spec["minimum"]) <= parsed <= int(spec["maximum"]):
            raise UserPreferenceLearningError(
                f"{name} 값은 {spec['minimum']}~{spec['maximum']} 범위여야 합니다."
            )
        return parsed
    if kind == "enum":
        parsed = str(value or "").strip().casefold()
        if parsed not in spec["values"]:
            raise UserPreferenceLearningError(f"{name} 값이 허용 목록에 없습니다.")
        return parsed
    if kind == "absolute_directory":
        parsed = os.path.abspath(os.path.expanduser(os.fspath(value)))
        if not os.path.isabs(parsed) or len(parsed) > 1_024:
            raise UserPreferenceLearningError("선호 파일 위치는 절대 경로여야 합니다.")
        return parsed
    if kind == "string_list":
        if not isinstance(value, (list, tuple, set)):
            raise UserPreferenceLearningError(f"{name} 값은 목록이어야 합니다.")
        allowed = set(spec["values"])
        parsed = []
        for item in value:
            clean = str(item or "").strip().casefold()
            if clean not in allowed:
                raise UserPreferenceLearningError(f"{name} 목록에 허용되지 않은 값이 있습니다.")
            if clean not in parsed:
                parsed.append(clean)
        if not parsed:
            raise UserPreferenceLearningError(f"{name} 목록이 비어 있습니다.")
        return parsed
    raise UserPreferenceLearningError(f"{name} 검증 규칙을 찾을 수 없습니다.")


def validate_preference_value(preference, value):
    """Return one canonical allowed value for downstream preference consumers."""
    name = _validate_preference_name(preference)
    return _validate_value(name, value)


def _validate_scope(kind, scope_id) -> tuple[str, str]:
    clean_kind = str(kind or "").strip().casefold()
    if clean_kind not in SCOPE_KINDS:
        raise UserPreferenceLearningError("사용자 선호 범위가 올바르지 않습니다.")
    if clean_kind == "global":
        return clean_kind, "global"
    if clean_kind == "app":
        clean_id = str(scope_id or "").strip().casefold()
        if clean_id not in APP_IDS:
            raise UserPreferenceLearningError("앱별 선호의 앱 이름이 올바르지 않습니다.")
        return clean_kind, clean_id
    if clean_kind == "workflow":
        clean_id = str(scope_id or "").strip().casefold()
        if not WORKFLOW_ID_RE.fullmatch(clean_id):
            raise UserPreferenceLearningError("업무별 선호 식별자가 올바르지 않습니다.")
        return clean_kind, clean_id
    clean_id = os.path.normcase(os.path.abspath(os.path.expanduser(os.fspath(scope_id))))
    if not clean_id or len(clean_id) > 1_024:
        raise UserPreferenceLearningError("파일별 선호 경로가 올바르지 않습니다.")
    return clean_kind, clean_id


class UserPreferenceLearningManager:
    """Promote repeated evidence to an active default only after approval."""

    def __init__(
        self,
        path=None,
        *,
        candidate_after=3,
        candidate_confidence=0.75,
    ):
        self.path = str(path or DEFAULT_LEARNING_PATH)
        self.candidate_after = max(2, int(candidate_after))
        self.candidate_confidence = max(0.5, min(float(candidate_confidence), 1.0))
        self._lock = threading.RLock()
        self._data = self._normalize(
            safe_read_json(self.path, self._default_data())
        )

    @staticmethod
    def _default_data():
        return {
            "schema_version": LEARNING_SCHEMA_VERSION,
            "candidates": {},
            "active_preferences": {},
        }

    @classmethod
    def _normalize(cls, value):
        raw = value if isinstance(value, dict) else {}
        candidates = {}
        for identifier, record in (
            raw.get("candidates", {}).items()
            if isinstance(raw.get("candidates"), dict)
            else []
        ):
            if not isinstance(record, dict):
                continue
            try:
                name = _validate_preference_name(record.get("preference"))
                scope_kind, scope_id = _validate_scope(
                    record.get("scope_kind"), record.get("scope_id")
                )
                expected_id = _candidate_id(scope_kind, scope_id, name)
                if str(identifier) != expected_id:
                    continue
                values = {}
                counts = {}
                raw_values = record.get("values", {})
                raw_counts = record.get("value_counts", {})
                if not isinstance(raw_values, dict) or not isinstance(raw_counts, dict):
                    continue
                for key, item in raw_values.items():
                    parsed = _validate_value(name, item)
                    canonical = _canonical(parsed)
                    if canonical != str(key):
                        continue
                    count = max(0, int(raw_counts.get(key, 0)))
                    if count:
                        values[canonical] = parsed
                        counts[canonical] = count
                proposed = record.get("proposed_value")
                proposed = _validate_value(name, proposed) if proposed is not None else None
            except (TypeError, ValueError, OSError):
                continue
            evidence_ids = [
                str(item) for item in list(record.get("evidence_ids") or [])[-200:]
                if EVIDENCE_ID_RE.fullmatch(str(item))
            ]
            status = str(record.get("status") or "observing").casefold()
            if status not in {"observing", "candidate", "active", "dismissed"}:
                status = "observing"
            candidates[expected_id] = {
                "candidate_id": expected_id,
                "preference": name,
                "scope_kind": scope_kind,
                "scope_id": scope_id,
                "values": values,
                "value_counts": counts,
                "evidence_ids": evidence_ids,
                "evidence_count": sum(counts.values()),
                "proposed_value": proposed,
                "proposed_count": max(counts.values(), default=0),
                "confidence": float(record.get("confidence") or 0.0),
                "status": status,
                "first_observed_at": record.get("first_observed_at"),
                "last_observed_at": record.get("last_observed_at"),
                "approved_at": record.get("approved_at"),
                "dismissed_at": record.get("dismissed_at"),
                "dismissed_at_count": max(0, int(record.get("dismissed_at_count") or 0)),
            }
        active = {}
        for identifier, record in (
            raw.get("active_preferences", {}).items()
            if isinstance(raw.get("active_preferences"), dict)
            else []
        ):
            candidate = candidates.get(str(identifier))
            if not candidate or not isinstance(record, dict):
                continue
            try:
                active[str(identifier)] = {
                    "candidate_id": str(identifier),
                    "preference": candidate["preference"],
                    "scope_kind": candidate["scope_kind"],
                    "scope_id": candidate["scope_id"],
                    "value": _validate_value(candidate["preference"], record.get("value")),
                    "approved_at": record.get("approved_at"),
                    "evidence_count": max(0, int(record.get("evidence_count") or 0)),
                    "user_confirmed": bool(record.get("user_confirmed", False)),
                }
            except (TypeError, ValueError, OSError):
                continue
        return {
            "schema_version": LEARNING_SCHEMA_VERSION,
            "candidates": candidates,
            "active_preferences": active,
        }

    def _save_locked(self):
        atomic_write_json(self.path, self._data, max_versions=3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def _decorate_candidate_locked(
        self,
        record: Mapping[str, Any],
        *,
        observed_value=None,
    ) -> dict[str, Any]:
        """Add an explainable, non-persistent view of active-value conflicts."""
        result = copy.deepcopy(dict(record))
        identifier = str(record.get("candidate_id") or "")
        active = self._data["active_preferences"].get(identifier)
        active_value = copy.deepcopy(active.get("value")) if active else None
        counts = dict(record.get("value_counts") or {})
        values = dict(record.get("values") or {})
        active_count = (
            int(counts.get(_canonical(active_value), 0))
            if active is not None
            else 0
        )
        alternatives = [
            (key, int(count))
            for key, count in counts.items()
            if active is not None and key != _canonical(active_value) and int(count) > 0
        ]
        alternatives.sort(key=lambda item: (-item[1], item[0]))
        conflicting_count = sum(count for _, count in alternatives)
        leading_key, leading_count = alternatives[0] if alternatives else (None, 0)
        leading_value = (
            copy.deepcopy(values.get(leading_key))
            if leading_key is not None
            else None
        )
        replacement = bool(
            active is not None
            and record.get("status") == "candidate"
            and record.get("proposed_value") != active_value
        )
        if active is None:
            conflict_state = "none"
        elif replacement:
            conflict_state = "replacement_candidate"
        elif conflicting_count:
            conflict_state = "active_challenged"
        else:
            conflict_state = "active_reinforced"
        result.update({
            "current_active_value": active_value,
            "active_evidence_count": active_count,
            "conflicting_evidence_count": conflicting_count,
            "leading_conflicting_value": leading_value,
            "leading_conflicting_count": leading_count,
            "conflict_state": conflict_state,
            "replacement_candidate": replacement,
        })
        if observed_value is not None:
            result["observed_value"] = copy.deepcopy(observed_value)
            result["observed_conflicts_with_active"] = bool(
                active is not None and observed_value != active_value
            )
        return result

    def get_candidate(self, candidate_id) -> dict[str, Any] | None:
        with self._lock:
            value = self._data["candidates"].get(str(candidate_id or ""))
            return self._decorate_candidate_locked(value) if value else None

    def record_evidence(
        self,
        preference,
        value,
        *,
        scope_kind,
        scope_id,
        evidence_id,
    ) -> dict[str, Any]:
        name = _validate_preference_name(preference)
        parsed_value = _validate_value(name, value)
        clean_scope_kind, clean_scope_id = _validate_scope(scope_kind, scope_id)
        clean_evidence_id = str(evidence_id or "").strip()
        if not EVIDENCE_ID_RE.fullmatch(clean_evidence_id):
            raise UserPreferenceLearningError("학습 증거 ID가 올바르지 않습니다.")
        identifier = _candidate_id(clean_scope_kind, clean_scope_id, name)
        now = _timestamp()
        with self._lock:
            record = self._data["candidates"].get(identifier)
            if record is None:
                record = {
                    "candidate_id": identifier,
                    "preference": name,
                    "scope_kind": clean_scope_kind,
                    "scope_id": clean_scope_id,
                    "values": {},
                    "value_counts": {},
                    "evidence_ids": [],
                    "evidence_count": 0,
                    "proposed_value": None,
                    "proposed_count": 0,
                    "confidence": 0.0,
                    "status": "observing",
                    "first_observed_at": now,
                    "last_observed_at": now,
                    "approved_at": None,
                    "dismissed_at": None,
                    "dismissed_at_count": 0,
                }
                self._data["candidates"][identifier] = record
            if clean_evidence_id in record["evidence_ids"]:
                result = self._decorate_candidate_locked(
                    record,
                    observed_value=parsed_value,
                )
                result["duplicate_evidence"] = True
                result["needs_confirmation"] = bool(
                    record["status"] == "candidate"
                    and record.get("proposed_value") == parsed_value
                )
                return result
            key = _canonical(parsed_value)
            record["values"][key] = parsed_value
            record["value_counts"][key] = int(record["value_counts"].get(key, 0)) + 1
            record["evidence_ids"].append(clean_evidence_id)
            record["evidence_ids"] = record["evidence_ids"][-200:]
            record["evidence_count"] = sum(record["value_counts"].values())
            highest = max(record["value_counts"].values())
            leaders = [
                item for item, count in record["value_counts"].items()
                if count == highest
            ]
            proposed_key = key if key in leaders else sorted(leaders)[0]
            record["proposed_value"] = record["values"][proposed_key]
            record["proposed_count"] = highest
            record["confidence"] = round(highest / record["evidence_count"], 4)
            record["last_observed_at"] = now
            active = self._data["active_preferences"].get(identifier)
            new_since_dismissal = record["evidence_count"] - int(
                record.get("dismissed_at_count") or 0
            )
            eligible = (
                highest >= self.candidate_after
                and record["confidence"] >= self.candidate_confidence
                and new_since_dismissal >= self.candidate_after
            )
            if active and active.get("value") == record["proposed_value"]:
                record["status"] = "active"
                active["evidence_count"] = record["evidence_count"]
            elif active:
                record["status"] = "candidate" if eligible else (
                    "dismissed" if record.get("dismissed_at") else "active"
                )
            else:
                record["status"] = "candidate" if eligible else (
                    "dismissed" if record.get("dismissed_at") else "observing"
                )
            self._save_locked()
            result = self._decorate_candidate_locked(
                record,
                observed_value=parsed_value,
            )
            result["duplicate_evidence"] = False
            result["needs_confirmation"] = bool(
                record["status"] == "candidate"
                and record.get("proposed_value") == parsed_value
            )
            return result

    def activate(self, candidate_id, *, expected_value=None) -> dict[str, Any]:
        identifier = str(candidate_id or "")
        with self._lock:
            record = self._data["candidates"].get(identifier)
            if not record or record.get("status") != "candidate":
                raise UserPreferenceLearningError("활성화할 사용자 선호 후보가 없습니다.")
            value = record.get("proposed_value")
            if expected_value is not None:
                expected = _validate_value(record["preference"], expected_value)
                if expected != value:
                    raise UserPreferenceLearningError("승인한 선호 값이 현재 후보와 다릅니다.")
            now = _timestamp()
            previous = self._data["active_preferences"].get(identifier)
            active = {
                "candidate_id": identifier,
                "preference": record["preference"],
                "scope_kind": record["scope_kind"],
                "scope_id": record["scope_id"],
                "value": copy.deepcopy(value),
                "approved_at": now,
                "evidence_count": record["evidence_count"],
                "user_confirmed": True,
            }
            self._data["active_preferences"][identifier] = active
            record["status"] = "active"
            record["approved_at"] = now
            record["dismissed_at"] = None
            record["dismissed_at_count"] = 0
            self._save_locked()
            result = copy.deepcopy(active)
            previous_value = copy.deepcopy(previous.get("value")) if previous else None
            result["replaced_previous_value"] = previous_value
            result["replaced_previous"] = bool(
                previous is not None and previous_value != value
            )
            return result

    def dismiss(self, candidate_id) -> bool:
        identifier = str(candidate_id or "")
        with self._lock:
            record = self._data["candidates"].get(identifier)
            if not record or record.get("status") != "candidate":
                return False
            record["status"] = "dismissed"
            record["dismissed_at"] = _timestamp()
            record["dismissed_at_count"] = int(record.get("evidence_count") or 0)
            self._save_locked()
            return True

    def deactivate(self, preference, *, scope_kind, scope_id) -> bool:
        name = _validate_preference_name(preference)
        clean_kind, clean_id = _validate_scope(scope_kind, scope_id)
        identifier = _candidate_id(clean_kind, clean_id, name)
        with self._lock:
            removed = self._data["active_preferences"].pop(identifier, None)
            record = self._data["candidates"].get(identifier)
            if record and record.get("status") == "active":
                record["status"] = "observing"
                record["approved_at"] = None
            if removed is not None:
                self._save_locked()
            return removed is not None

    def resolve(
        self,
        preference,
        *,
        app_id=None,
        workflow_id=None,
        file_path=None,
    ) -> dict[str, Any] | None:
        name = _validate_preference_name(preference)
        scopes = []
        if file_path:
            scopes.append(_validate_scope("file", file_path))
        if workflow_id:
            scopes.append(_validate_scope("workflow", workflow_id))
        if app_id:
            scopes.append(_validate_scope("app", app_id))
        scopes.append(("global", "global"))
        with self._lock:
            for kind, identifier in scopes:
                key = _candidate_id(kind, identifier, name)
                record = self._data["active_preferences"].get(key)
                if record and record.get("user_confirmed"):
                    result = copy.deepcopy(record)
                    result["resolved_scope"] = kind
                    return result
        return None

    def list_candidates(self, *, include_observing=False) -> list[dict[str, Any]]:
        with self._lock:
            values = []
            for record in self._data["candidates"].values():
                if not include_observing and record.get("status") not in {
                    "candidate", "active"
                }:
                    continue
                values.append(self._decorate_candidate_locked(record))
            return sorted(
                values,
                key=lambda item: (
                    item.get("status") != "candidate",
                    str(item.get("last_observed_at") or ""),
                ),
            )

    def active_preferences(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in self._data["active_preferences"].values()
                if item.get("user_confirmed")
            ]
