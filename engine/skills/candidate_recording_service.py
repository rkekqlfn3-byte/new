"""Record privacy-bounded native-action candidate observations."""

from __future__ import annotations

import json
import os
import uuid

from engine.managers.native_action_candidate_manager import (
    DOCUMENT_SLOT_TYPES,
    NativeActionCandidateManager,
)


class CandidateRecordingService:
    """Own candidate-store selection and success/failure telemetry."""

    def __init__(self, owner):
        self.owner = owner

    def manager(self):
        if self.owner._native_candidate_manager_injected:
            return self.owner.native_action_candidate_manager
        dictionary_path = os.path.abspath(self.owner.dict_mgr.dictionary_path)
        expected_path = os.path.join(
            os.path.dirname(dictionary_path), "native_action_candidates.json"
        )
        current_path = os.path.abspath(
            self.owner.native_action_candidate_manager.path
        )
        if os.path.normcase(current_path) != os.path.normcase(expected_path):
            self.owner.native_action_candidate_manager = (
                NativeActionCandidateManager(expected_path)
            )
        return self.owner.native_action_candidate_manager

    def execution_id(self):
        return self.owner._current_execution_id() or uuid.uuid4().hex

    def signature_for(self, app_name, learning):
        return self.manager().signature_for(app_name, learning)

    def list_candidates(self, include_observing=True, include_dismissed=False):
        return self.manager().list_candidates(
            include_observing=bool(include_observing),
            include_dismissed=bool(include_dismissed),
        )

    def set_status(self, candidate_id, status, reason=""):
        return self.manager().set_status(candidate_id, status, reason=reason)

    def get_implementation_spec(self, candidate_id):
        return self.manager().get_implementation_spec(candidate_id)

    @staticmethod
    def _document_identity(
        learned_macro, execution_result=None, *, slots=None, argument=""
    ):
        """Extract a document identity transiently; the manager hashes it."""
        result = execution_result if isinstance(execution_result, dict) else {}
        data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        prepared = data.get("prepared_action", {})
        if isinstance(prepared, dict) and prepared.get("document_id"):
            return str(prepared["document_id"])
        for key in ("document_id", "document_fingerprint"):
            if data.get(key):
                return str(data[key])

        runtime = dict(slots) if isinstance(slots, dict) else {}
        if not runtime and isinstance(argument, str) and argument.strip().startswith("{"):
            try:
                decoded = json.loads(argument)
                if isinstance(decoded, dict):
                    runtime = decoded
            except (TypeError, ValueError, json.JSONDecodeError):
                runtime = {}
        learning = learned_macro.get("learning", {}) if isinstance(
            learned_macro, dict
        ) else {}
        schema = learning.get("slots", []) if isinstance(learning, dict) else []
        identities = {}
        for item in schema if isinstance(schema, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            slot_type = str(item.get("type") or "").strip().casefold()
            document_like = (
                slot_type in DOCUMENT_SLOT_TYPES
                or any(
                    part in name.casefold()
                    for part in ("file", "path", "document", "workbook", "문서", "파일")
                )
            )
            value = runtime.get(name)
            if document_like and name in runtime and value is not None and value != "":
                identities[name] = str(value)
        if not identities:
            return ""
        return json.dumps(
            identities, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def record_confirmed_learning(
        self,
        app_name,
        learning,
        description,
        *,
        execution_id="",
        verified=False,
        document_id="",
        observation_kind="user",
    ):
        try:
            return self.manager().record_success(
                app_name,
                learning,
                description,
                source="confirmed_dynamic_code",
                execution_id=execution_id or self.execution_id(),
                user_confirmed=True,
                verified=bool(verified),
                selected_route="python",
                document_id=document_id,
                observation_kind=observation_kind,
            )
        except OSError:
            # Candidate telemetry must never invalidate a saved skill.
            return None

    @staticmethod
    def _eligible_dynamic_skill(learned_macro):
        return (
            isinstance(learned_macro, dict)
            and bool(str(learned_macro.get("code", "")).strip())
            and learned_macro.get("verification_status")
            in {"verified", "passed", "user_confirmed"}
        )

    def record_success(
        self,
        app_name,
        learned_macro,
        *,
        source="learned_dynamic",
        execution_result=None,
        selected_route="python",
        slots=None,
        argument="",
        observation_kind="user",
    ):
        if not self._eligible_dynamic_skill(learned_macro):
            return None
        result = execution_result if isinstance(execution_result, dict) else {}
        verified = bool(result.get("verified")) or (
            result.get("verification_status") in {"verified", "passed"}
        )
        document_id = self._document_identity(
            learned_macro,
            result,
            slots=slots,
            argument=argument,
        )
        try:
            return self.manager().record_success(
                app_name,
                learned_macro.get("learning", {}),
                learned_macro.get("description", ""),
                source=source,
                execution_id=self.execution_id(),
                user_confirmed=False,
                verified=verified,
                selected_route=selected_route,
                document_id=document_id,
                observation_kind=observation_kind,
            )
        except OSError:
            return None

    def record_failure(
        self,
        app_name,
        learned_macro,
        *,
        source="learned_dynamic",
        selected_route="python",
        observation_kind="user",
    ):
        if not self._eligible_dynamic_skill(learned_macro):
            return None
        try:
            return self.manager().record_failure(
                app_name,
                learned_macro.get("learning", {}),
                source=source,
                execution_id=self.execution_id(),
                selected_route=selected_route,
                observation_kind=observation_kind,
            )
        except OSError:
            return None
