"""Review, approve, persist, or discard reusable skill candidates."""

from __future__ import annotations

import re

from engine.learning_quality import clean_trigger_list, find_trigger_conflicts
from engine.learning_schema import literal_utterances, normalize_learning_metadata
from engine.skills.postconditions import normalize_postconditions
from engine.skills.skill_profile import SkillProfile
from engine.skills.run_policy import ensure_run_policy_fields


class SkillLearningService:
    """Own the pending-learning queue and its persistence transaction."""

    def __init__(self, owner):
        self.owner = owner

    @property
    def pending(self):
        return self.owner.pending_macros

    @property
    def dict_mgr(self):
        return self.owner.dict_mgr

    def stage_candidate(self, candidate):
        if not isinstance(candidate, dict):
            raise ValueError("학습 후보 정보가 올바르지 않습니다.")
        self.pending.append(candidate)
        return candidate

    def make_unique_macro_name(self, app_name, suggested_name):
        cleaned = re.sub(
            r"[^0-9a-zA-Z가-힣_-]+", "_", str(suggested_name or "").strip()
        ).strip("_-")
        base_name = cleaned or "learned_macro"
        learned_for_app = getattr(self.dict_mgr, "learned_macros", {}).get(
            app_name, {}
        )
        taken = set(self.dict_mgr.macro_dict)
        if isinstance(learned_for_app, dict):
            taken.update(learned_for_app)
        taken.update(macro.get("name") for macro in self.pending)

        if base_name not in taken:
            return base_name
        suffix = 2
        while f"{base_name}_{suffix}" in taken:
            suffix += 1
        return f"{base_name}_{suffix}"

    def get_pending_review(self):
        candidates = []
        for index, macro in enumerate(self.pending):
            learning = macro.get("learning", {})
            plan = macro.get("plan", [])
            candidates.append({
                "index": index,
                "name": macro.get("name", ""),
                "app": macro.get("app", "시스템"),
                "description": macro.get("desc", ""),
                "kind": "action_plan" if plan else "dynamic_code",
                "intent": learning.get("intent", "LEARNED_ACTION"),
                "verbs": list(learning.get("verbs", [])),
                "utterances": list(learning.get("utterances", [])),
                "nouns": list(learning.get("nouns", [])),
                "slots": list(learning.get("slots", [])),
                "plan": list(plan),
                "explanation_steps": list(macro.get("steps", [])),
                "verification_status": macro.get(
                    "verification_status", "confirmation_required"
                ),
            })
        return {"pending": bool(candidates), "candidates": candidates}

    @staticmethod
    def clean_review_macro_name(value):
        return re.sub(
            r"[^0-9a-zA-Z가-힣_-]+", "_", str(value or "").strip()
        ).strip("_-")[:100]

    def apply_review_edits(self, edits):
        if edits is None:
            return
        if not isinstance(edits, list):
            raise ValueError("학습 수정값은 배열 형식이어야 합니다.")
        by_index = {
            item.get("index"): item for item in edits
            if isinstance(item, dict) and isinstance(item.get("index"), int)
        }
        prepared = []
        unchanged_names = {
            macro.get("name") for index, macro in enumerate(self.pending)
            if index not in by_index and macro.get("name")
        }
        taken = set(self.dict_mgr.macro_dict) | unchanged_names
        for index, macro in enumerate(self.pending):
            edit = by_index.get(index)
            if not edit:
                prepared.append(dict(macro))
                continue
            base_name = self.clean_review_macro_name(edit.get("name"))
            if not base_name:
                raise ValueError(f"{index + 1}번 매크로 이름이 비어 있습니다.")
            macro_name = base_name
            suffix = 2
            while macro_name in taken:
                macro_name = f"{base_name}_{suffix}"
                suffix += 1

            raw_learning = dict(macro.get("learning", {}))
            utterances = edit.get("utterances")
            verbs = edit.get("verbs")
            if not isinstance(utterances, list) or not any(
                isinstance(value, str) and value.strip() for value in utterances
            ):
                raise ValueError(f"{index + 1}번 발동 문장을 하나 이상 입력하세요.")
            if not isinstance(verbs, list):
                raise ValueError(f"{index + 1}번 동사 값이 올바르지 않습니다.")
            raw_learning["utterances"] = utterances
            raw_learning["verbs"] = verbs
            updated = dict(macro)
            updated["name"] = macro_name
            updated["learning"] = normalize_learning_metadata(
                {"learning": raw_learning}
            )
            safe_utterances = clean_trigger_list(
                updated["learning"].get("utterances", []), macro_name
            )
            if not safe_utterances:
                raise ValueError(
                    f"{index + 1}번에 자연스러운 발동 문장을 하나 이상 입력하세요."
                )
            updated["learning"]["utterances"] = safe_utterances
            updated["utterances"] = updated["learning"].get("utterances", [])
            prepared.append(updated)
            taken.add(macro_name)

        staged = {}
        for index, macro in enumerate(prepared):
            utterances = macro.get("learning", {}).get("utterances", [])
            conflicts = find_trigger_conflicts(
                self.dict_mgr.learned_macros,
                utterances,
                exclude=(macro.get("app"), macro.get("name")),
            )
            if conflicts:
                conflict = conflicts[0]
                raise ValueError(
                    f"{index + 1}번 발동 문장 '{conflict['utterance']}'이 "
                    f"[{conflict['app']}/{conflict['macro']}]와 충돌합니다."
                )
            pending_conflicts = find_trigger_conflicts(
                staged,
                utterances,
                exclude=(macro.get("app"), macro.get("name")),
            )
            if pending_conflicts:
                conflict = pending_conflicts[0]
                raise ValueError(
                    f"{index + 1}번 발동 문장이 다른 학습 후보와 충돌합니다: "
                    f"{conflict['utterance']}"
                )
            staged.setdefault(macro.get("app"), {})[macro.get("name")] = {
                "learning": macro.get("learning", {})
            }
        self.owner.pending_macros = prepared

    def approve(self, edits=None):
        with self.dict_mgr.locked():
            return self.approve_locked(edits)

    def approve_locked(self, edits=None):
        if not self.pending:
            raise ValueError("저장할 학습 후보가 없습니다.")
        self.apply_review_edits(edits)
        if not hasattr(self.dict_mgr, "learned_macros"):
            self.dict_mgr.learned_macros = {}

        saved_count = 0
        native_candidate_observations = []
        for macro in self.pending:
            app_name = macro["app"]
            macro_name = macro["name"]
            learning = normalize_learning_metadata({
                "learning": macro.get("learning", {}),
                "description": macro.get("desc", ""),
            })
            macro["learning"] = learning
            utterances = literal_utterances(learning)
            if not utterances and not any(
                "{" in text for text in learning.get("utterances", [])
            ):
                raise ValueError("자연스러운 발동 문장을 하나 이상 입력하세요.")
            verification_status = macro.get(
                "verification_status", "confirmation_required"
            )
            if verification_status in {
                "confirmation_required", "manual_confirmation_required"
            }:
                verification_status = "user_confirmed"

            candidate_signature = ""
            if str(macro.get("code", "")).strip():
                candidate_signature = (
                    self.owner.candidate_recording_service.signature_for(
                        app_name, learning
                    )
                )
            app_dict = self.dict_mgr.learned_macros.setdefault(app_name, {})
            learned_record = {
                "description": macro["desc"],
                "code": macro.get("code", ""),
                "plan": macro.get("plan", []),
                "explanation_steps": macro.get("steps", []) or [],
                "utterances": utterances,
                "default_target": macro.get("target", ""),
                "learning": learning,
                "verification_status": verification_status,
                "verification": macro.get("verification", []),
                "usage_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "last_used_at": "",
                "last_status": "never",
                "state": "active",
                "state_reason": "",
                "consecutive_failures": 0,
                "last_failure_type": "",
            }
            ensure_run_policy_fields(
                learned_record, changed_by="system_default"
            )
            for plan_key in ("native_plan", "uia_plan"):
                if isinstance(macro.get(plan_key), list) and macro.get(plan_key):
                    learned_record[plan_key] = list(macro[plan_key])
            raw_profile = macro.get("execution_profile")
            if isinstance(raw_profile, dict):
                profile = SkillProfile.from_skill({
                    "plan": learned_record["plan"],
                    "native_plan": learned_record.get("native_plan", []),
                    "uia_plan": learned_record.get("uia_plan", []),
                    "code": learned_record["code"],
                    "execution_profile": raw_profile,
                })
                learned_record["execution_profile"] = {
                    "primary_route": profile.primary_route,
                    "fallback_routes": list(profile.fallback_routes),
                    "verification_required": profile.verification_required,
                    "max_fallback_attempts": profile.max_fallback_attempts,
                }
            postconditions = normalize_postconditions(
                macro.get("postconditions", [])
            )
            if postconditions:
                learned_record["postconditions"] = postconditions
            if candidate_signature:
                learned_record["native_candidate_signature"] = candidate_signature
                native_candidate_observations.append({
                    "app": app_name,
                    "learning": learning,
                    "description": macro["desc"],
                    "execution_id": macro.get("candidate_execution_id", ""),
                    "verified": verification_status in {"verified", "passed"},
                })
            app_dict[macro_name] = learned_record
            macro_entry = self.dict_mgr.macro_dict.setdefault(macro_name, {})
            existing_synonyms = macro_entry.setdefault("synonyms", [])
            for utterance in utterances:
                if utterance not in existing_synonyms:
                    existing_synonyms.append(utterance)
            macro_entry.update({
                "name": macro_name,
                "description": f"[{app_name}] {macro['desc']}",
                "type": "learned",
                "app": app_name,
                "intent": learning.get("intent", "LEARNED_ACTION"),
                "verbs": learning.get("verbs", []),
                "slots": learning.get("slots", []),
                "state": "active",
            })
            self.dict_mgr.add_verified_noun_aliases(
                learning.get("nouns", []), save=False
            )
            saved_count += 1

        self.dict_mgr.save()
        self.owner.template_matcher.invalidate()
        for observation in native_candidate_observations:
            self.owner.candidate_recording_service.record_confirmed_learning(
                observation["app"],
                observation["learning"],
                observation["description"],
                execution_id=observation["execution_id"],
                verified=bool(observation.get("verified")),
            )
        self.owner.pending_macros = []
        return (
            f"학습 완료! {saved_count}개의 행동을 저장했습니다. "
            "다음부터 같은 문장이나 템플릿은 로컬에서 찾고 실행 전 확인합니다."
        )

    def reject(self, reason="discard"):
        count = len(self.pending)
        self.owner.pending_macros = []
        if reason == "run_once":
            return f"이번 한 번만 실행하고 학습 후보 {count}개는 저장하지 않았습니다."
        return f"학습 후보 {count}개를 폐기했습니다."
