"""Privacy-bounded observations and review workflow for native-action ideas.

Candidate records are development specifications only. They never contain
Python source, concrete slot values, raw commands, document paths, or document
contents, and they never create or install executable functionality.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json


CANDIDATES_PATH = user_data_path("native_action_candidates.json")
SCHEMA_VERSION = 3
DEFAULT_THRESHOLD = 3
MAX_FAILURE_RATE = 0.20
GENERIC_INTENTS = {"", "LEARNED_ACTION", "DYNAMIC_CODE", "UNKNOWN"}
CANDIDATE_STATUSES = frozenset({
    "observing", "ready_for_review", "accepted", "rejected",
    "implemented", "merged",
})
CLOSED_STATUSES = frozenset({"rejected", "implemented", "merged"})
RISKY_INTENT_PARTS = {
    "DELETE", "REMOVE", "ERASE", "DESTROY", "SHUTDOWN", "REBOOT",
    "FORMAT_DISK", "UPLOAD", "SEND_EMAIL", "EXTERNAL_SEND", "PAY",
    "PURCHASE", "TRANSFER_MONEY", "CREDENTIAL", "PASSWORD",
}
MEDIUM_RISK_INTENT_PARTS = {
    "WRITE", "SET", "UPDATE", "REPLACE", "FORMAT", "SORT", "FILTER",
    "MOVE", "EXPORT", "SAVE", "INSERT", "COPY", "CREATE",
}
BROAD_INTENTS = frozenset({
    "PROCESS_DOCUMENT", "EDIT_DOCUMENT", "AUTOMATE_APP", "MANAGE_DATA",
    "HANDLE_FILE", "PROCESS_DATA", "EDIT_FILE",
})
DOCUMENT_SLOT_TYPES = frozenset({
    "file", "path", "document", "workbook", "spreadsheet", "presentation",
})
_COUNTER_FIELDS = (
    "process_success_count", "verified_success_count", "user_confirmed_count",
    "failure_count", "consecutive_process_success",
    "consecutive_verified_success", "user_observation_count",
    "test_process_success_count", "test_verified_success_count",
    "test_failure_count",
)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _bounded_text(value, limit):
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _privacy_text(value, limit):
    text = _bounded_text(value, limit * 3)
    text = re.sub(r"(?i)https?://[^\s\"']+", "{url}", text)
    text = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "{email}", text)
    text = re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\).+$", "{path}", text)
    text = re.sub(
        r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+",
        "{secret}",
        text,
    )
    return _bounded_text(text, limit)


def _normalize_app(value):
    normalized = _bounded_text(value, 80).casefold()
    aliases = {
        "엑셀": "excel", "microsoft excel": "excel", "ms excel": "excel",
        "한글": "hwp", "한컴 한글": "hwp", "hanword": "hwp",
        "시스템": "system",
    }
    return aliases.get(normalized, normalized) or "system"


def _normalize_intent(value):
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^0-9A-Z_]+", "_", raw).strip("_")[:64]


def _normalize_route(value):
    route = _bounded_text(value, 30).casefold()
    return route if route in {"native", "action_plan", "uia", "python"} else ""


def _slot_schema(learning):
    slots = learning.get("slots", []) if isinstance(learning, dict) else []
    result = []
    seen = set()
    for item in slots if isinstance(slots, list) else []:
        if not isinstance(item, dict):
            continue
        name = re.sub(
            r"[^0-9a-zA-Z가-힣_]+", "_", str(item.get("name", "")).strip()
        ).strip("_")[:50]
        slot_type = re.sub(
            r"[^0-9a-zA-Z_]+", "_", str(item.get("type", "value")).casefold()
        ).strip("_")[:30] or "value"
        key = (name.casefold(), slot_type)
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({
            "name": name,
            "type": slot_type,
            "required": bool(item.get("required", True)),
        })
    return sorted(result, key=lambda item: (item["name"].casefold(), item["type"]))[:16]


def _merge_slot_schemas(*schemas):
    merged = {}
    for schema in schemas:
        normalized = _slot_schema({"slots": schema})
        for item in normalized:
            key = (item["name"].casefold(), item["type"])
            previous = merged.get(key)
            if previous is None:
                merged[key] = item
            else:
                previous["required"] = bool(
                    previous.get("required", True) or item.get("required", True)
                )
    return sorted(
        merged.values(), key=lambda item: (item["name"].casefold(), item["type"])
    )[:16]


def _template_structure(value):
    placeholders = re.findall(
        r"\{[0-9a-zA-Z가-힣_]{1,50}\}", str(value or "")
    )
    return " ".join(dict.fromkeys(placeholders))[:180]


def _utterance_templates(learning):
    values = learning.get("utterances", []) if isinstance(learning, dict) else []
    result = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        text = _template_structure(value).casefold()
        # Store only placeholder structure. Even the literal parts of a
        # template can contain customer names or document content.
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result[:5]


def _is_risky_intent(intent):
    padded = f"_{intent}_"
    return any(
        part == intent or f"_{part}_" in padded or intent.startswith(f"{part}_")
        for part in RISKY_INTENT_PARTS
    )


def _contains_intent_part(intent, parts):
    tokens = set(str(intent or "").split("_"))
    return any(part in tokens for part in parts)


def _candidate_characteristics(intent, app):
    intent = _normalize_intent(intent)
    if _is_risky_intent(intent):
        risk_level = "high"
    elif _contains_intent_part(intent, MEDIUM_RISK_INTENT_PARTS):
        risk_level = "medium"
    else:
        risk_level = "low"

    if _contains_intent_part(intent, {"DELETE", "REMOVE", "ERASE"}):
        change_type = "data_deletion"
        verification_hint = "실행 전후 대상 항목 수와 잔여 항목을 비교"
    elif _contains_intent_part(intent, {"EXPORT", "SAVE"}):
        change_type = "file_creation"
        verification_hint = "결과 파일의 존재와 예상 형식을 다시 확인"
    elif _contains_intent_part(intent, {"FORMAT", "STYLE"}):
        change_type = "format_change"
        verification_hint = "대상 범위의 실제 서식을 다시 읽어 비교"
    elif _contains_intent_part(intent, {"WRITE", "SET", "UPDATE", "REPLACE", "INSERT"}):
        change_type = "content_change"
        verification_hint = "변경 대상의 최종 값 또는 내용을 다시 읽어 비교"
    elif _contains_intent_part(intent, {"MOVE", "WINDOW"}):
        change_type = "window_or_location_change"
        verification_hint = "대상의 최종 위치 또는 창 상태를 다시 확인"
    elif _contains_intent_part(intent, {"READ", "GET", "QUERY", "CALCULATE"}):
        change_type = "read_or_calculation"
        verification_hint = "동일 입력에 대한 구조화 결과와 예상 조건을 비교"
    else:
        change_type = "local_action"
        verification_hint = "명시적 공통 사후조건으로 최종 상태를 확인"

    if app == "excel":
        location = "ExcelAdapter"
    elif app == "hwp":
        location = "HwpAdapter"
    elif app == "system":
        location = "ActionExecutor"
    else:
        location = f"{re.sub(r'[^0-9A-Za-z]+', '', app.title()) or 'App'}Adapter"
    reversibility = {"low": "easy", "medium": "limited", "high": "hard"}[
        risk_level
    ]
    return {
        "risk_level": risk_level,
        "reversibility": reversibility,
        "rollback_required": risk_level != "low",
        "change_type": change_type,
        "verification_hint": verification_hint,
        "recommended_implementation_location": location,
        "scope_review_required": intent in BROAD_INTENTS,
    }


def _counter(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_identifier(value, fallback="native_action"):
    normalized = _normalize_intent(value).casefold()
    return normalized or fallback


def _reason_identifier(value, fallback=""):
    return re.sub(
        r"[^0-9a-z_]+", "_", str(value or "").strip().casefold()
    ).strip("_")[:60] or fallback


def _migrate_candidate_record(value):
    """Normalize schema 1/2 records without inventing verification evidence."""
    original = value if isinstance(value, dict) else {}
    record = dict(original)
    mappings = {
        "process_success_count": "success_count",
        "user_confirmed_count": "confirmed_success_count",
        "consecutive_process_success": "consecutive_successes",
    }
    for current, legacy in mappings.items():
        if current not in record:
            record[current] = _counter(record.get(legacy, 0))
    defaults = {
        "verified_success_count": 0,
        "failure_count": _counter(record.get("failure_count", 0)),
        "consecutive_verified_success": 0,
        "user_observation_count": (
            _counter(record.get("process_success_count"))
            + _counter(record.get("failure_count"))
        ),
        "test_process_success_count": 0,
        "test_verified_success_count": 0,
        "test_failure_count": 0,
    }
    for key, default in defaults.items():
        record[key] = _counter(record.get(key, default))
    for key in _COUNTER_FIELDS:
        record[key] = _counter(record.get(key, 0))
    for legacy in ("success_count", "confirmed_success_count", "consecutive_successes"):
        record.pop(legacy, None)

    intent = _normalize_intent(record.get("normalized_intent") or record.get("intent"))
    app = _normalize_app(record.get("target_app") or record.get("app"))
    schema = _merge_slot_schemas(
        record.get("slot_schema", []), record.get("required_slots", [])
    )
    record.update({
        "normalized_intent": intent,
        "intent": intent,
        "target_app": app,
        "app": app,
        "slot_schema": schema,
        "required_slots": [item for item in schema if item.get("required", True)],
        "selected_route": _normalize_route(record.get("selected_route")) or "python",
        "suggested_native_action_name": _safe_identifier(
            record.get("suggested_native_action_name") or intent
        ),
    })
    characteristics = _candidate_characteristics(intent, app)
    for key, default in characteristics.items():
        if key not in record or record.get(key) in {None, ""}:
            record[key] = default
    record["rollback_required"] = bool(record.get("rollback_required", False))

    status = str(record.get("status") or "observing").strip().casefold()
    if status == "dismissed":
        status = "rejected"
        record["review_reason"] = "legacy_dismissed"
    if status not in CANDIDATE_STATUSES:
        status = "observing"
    record["status"] = status
    # Descriptions used to be AI-authored free text. Stage 11 replaces them
    # with a deterministic label so migrated records cannot retain document
    # text or concrete values.
    record["description"] = _safe_identifier(intent).replace("_", " ")
    record["utterance_templates"] = sorted({
        _template_structure(item).casefold()
        for item in record.get("utterance_templates", [])
        if isinstance(item, str) and _template_structure(item)
    })[:5]
    for key in ("sources", "evidence_ids", "document_fingerprints", "status_history"):
        if not isinstance(record.get(key), list):
            record[key] = []
    record["sources"] = [
        _reason_identifier(item) for item in record["sources"]
        if _reason_identifier(item)
    ][:20]
    record["evidence_ids"] = [
        re.sub(r"[^0-9a-zA-Z_-]+", "_", str(item)).strip("_")[:100]
        for item in record["evidence_ids"]
        if str(item or "").strip()
    ][:100]
    record["document_fingerprints"] = [
        item for item in record["document_fingerprints"]
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
    ][:100]
    record["distinct_document_count"] = len(set(record["document_fingerprints"]))
    if not isinstance(record.get("implementation_spec"), dict):
        record["implementation_spec"] = {}
    if not isinstance(record.get("status_history"), list):
        record["status_history"] = []
    record["status_history"] = [
        {
            "previous_status": str(item.get("previous_status") or "observing")
            if str(item.get("previous_status") or "observing") in CANDIDATE_STATUSES
            else "observing",
            "new_status": str(item.get("new_status") or "observing")
            if str(item.get("new_status") or "observing") in CANDIDATE_STATUSES
            else "observing",
            "changed_at": _bounded_text(item.get("changed_at"), 40),
            "reason": _reason_identifier(item.get("reason"), "migration"),
        }
        for item in record["status_history"]
        if isinstance(item, dict)
    ][-30:]
    record["review_reason"] = _reason_identifier(
        record.get("review_reason"), ""
    )
    record["macro_sufficiency"] = (
        record.get("macro_sufficiency")
        if record.get("macro_sufficiency") in {"undetermined", "sufficient", "insufficient"}
        else "undetermined"
    )
    return record, record != original


class NativeActionCandidateManager:
    """Persist and review privacy-bounded Python-to-native candidates."""

    def __init__(
        self, path=None, threshold=DEFAULT_THRESHOLD, existing_native_actions=None
    ):
        self.path = path or CANDIDATES_PATH
        self.threshold = max(3, int(threshold or DEFAULT_THRESHOLD))
        self._lock = threading.RLock()
        self._existing_native_actions = self._normalize_native_actions(
            existing_native_actions
        )
        loaded = safe_read_json(
            self.path, {"schema_version": SCHEMA_VERSION, "candidates": {}}
        )
        candidates = loaded.get("candidates", {}) if isinstance(loaded, dict) else {}
        raw_candidates = candidates if isinstance(candidates, dict) else {}
        self._candidates = {}
        migrated = not isinstance(loaded, dict) or _counter(
            loaded.get("schema_version", 1)
        ) != SCHEMA_VERSION
        for old_signature, value in raw_candidates.items():
            record, changed = _migrate_candidate_record(value)
            signature = self.signature_for(
                record.get("target_app"),
                {"intent": record.get("normalized_intent")},
            ) or _bounded_text(record.get("signature") or old_signature, 64)
            if not signature:
                migrated = True
                continue
            if record.get("signature") != signature:
                record["signature"] = signature
                changed = True
            candidate_id = signature[:16]
            if record.get("candidate_id") != candidate_id:
                record["candidate_id"] = candidate_id
                changed = True
            if signature in self._candidates:
                self._merge_records(self._candidates[signature], record)
                changed = True
            else:
                self._candidates[signature] = record
            migrated = migrated or changed or signature != old_signature
        for record in self._candidates.values():
            previous = dict(record)
            self._refresh_review_status(record)
            migrated = migrated or previous != record
        if migrated:
            self._save()

    @staticmethod
    def _normalize_native_actions(value):
        if isinstance(value, dict):
            return {
                _normalize_app(app): {
                    _safe_identifier(operation) for operation in operations
                }
                for app, operations in value.items()
                if isinstance(operations, (set, frozenset, list, tuple))
            }
        try:
            from engine.action_registry import ACTION_SPECS
            from engine.app_actions.excel_adapter import ExcelAdapter
            from engine.app_actions.hwp_adapter import HwpAdapter
            return {
                "excel": set(ExcelAdapter.supported_operations),
                "hwp": set(HwpAdapter.supported_operations),
                "system": set(ACTION_SPECS),
            }
        except Exception:
            return {}

    @staticmethod
    def signature_for(app, learning):
        intent = _normalize_intent(
            learning.get("intent") if isinstance(learning, dict) else ""
        )
        if intent in GENERIC_INTENTS or _is_risky_intent(intent):
            return ""
        # Slot-only variants are one development idea, not separate candidates.
        payload = {"app": _normalize_app(app), "intent": intent}
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _save(self):
        atomic_write_json(
            self.path,
            {"schema_version": SCHEMA_VERSION, "candidates": self._candidates},
        )

    @staticmethod
    def _failure_rate(record):
        successes = _counter(record.get("process_success_count"))
        failures = _counter(record.get("failure_count"))
        total = successes + failures
        return round(failures / total, 4) if total else 0.0

    def _base_record(self, signature, app, learning, description, selected_route):
        intent = _normalize_intent(learning.get("intent", ""))
        app = _normalize_app(app)
        now = _now()
        schema = _slot_schema(learning)
        record = {
            "candidate_id": signature[:16],
            "signature": signature,
            "normalized_intent": intent,
            "intent": intent,
            "target_app": app,
            "app": app,
            "description": _safe_identifier(intent).replace("_", " "),
            "required_slots": [item for item in schema if item.get("required", True)],
            "slot_schema": schema,
            "utterance_templates": _utterance_templates(learning),
            "selected_route": selected_route,
            "status": "observing",
            "threshold": self.threshold,
            "process_success_count": 0,
            "verified_success_count": 0,
            "user_confirmed_count": 0,
            "failure_count": 0,
            "consecutive_process_success": 0,
            "consecutive_verified_success": 0,
            "user_observation_count": 0,
            "test_process_success_count": 0,
            "test_verified_success_count": 0,
            "test_failure_count": 0,
            "distinct_document_count": 0,
            "first_seen_at": now,
            "last_seen_at": now,
            "ready_at": "",
            "accepted_at": "",
            "implemented_at": "",
            "merged_at": "",
            "review_reason": "",
            "macro_sufficiency": "undetermined",
            "suggested_native_action_name": _safe_identifier(intent),
            "implementation_spec": {},
            "status_history": [],
            "sources": [],
            "evidence_ids": [],
            "document_fingerprints": [],
        }
        record.update(_candidate_characteristics(intent, app))
        return record

    @staticmethod
    def _merge_records(target, incoming):
        consecutive_fields = {
            "consecutive_process_success", "consecutive_verified_success"
        }
        target_consecutive = {
            key: _counter(target.get(key)) for key in consecutive_fields
        }
        for key in _COUNTER_FIELDS:
            if key in consecutive_fields:
                continue
            target[key] = _counter(target.get(key)) + _counter(incoming.get(key))
        for key in consecutive_fields:
            target[key] = max(
                target_consecutive[key], _counter(incoming.get(key))
            )
        schema = _merge_slot_schemas(
            target.get("slot_schema", []), incoming.get("slot_schema", [])
        )
        target["slot_schema"] = schema
        target["required_slots"] = [
            item for item in schema if item.get("required", True)
        ]
        for key, limit in (
            ("sources", 20), ("evidence_ids", 100),
            ("document_fingerprints", 100), ("utterance_templates", 5),
        ):
            values = []
            for item in list(target.get(key, [])) + list(incoming.get(key, [])):
                if item not in values:
                    values.append(item)
            target[key] = values[-limit:]
        target["first_seen_at"] = min(
            filter(None, [target.get("first_seen_at"), incoming.get("first_seen_at")]),
            default="",
        )
        target["last_seen_at"] = max(
            target.get("last_seen_at", ""), incoming.get("last_seen_at", "")
        )
        if not target.get("description"):
            target["description"] = incoming.get("description", "")
        priority = {
            "observing": 0, "ready_for_review": 1, "rejected": 2,
            "accepted": 3, "merged": 4, "implemented": 5,
        }
        statuses = [target.get("status", "observing"), incoming.get("status", "observing")]
        target["status"] = max(statuses, key=lambda item: priority.get(item, 0))
        if not target.get("implementation_spec") and incoming.get("implementation_spec"):
            target["implementation_spec"] = dict(incoming["implementation_spec"])
        target["distinct_document_count"] = len(
            set(target.get("document_fingerprints", []))
        )

    def _existing_native_operation(self, record):
        app = _normalize_app(record.get("target_app"))
        operations = self._existing_native_actions.get(app, set())
        intent = _safe_identifier(record.get("normalized_intent"))
        candidates = [intent]
        prefix = f"{app}_"
        if intent.startswith(prefix):
            candidates.append(intent[len(prefix):])
        return next((item for item in candidates if item in operations), "")

    def _readiness(self, record):
        general = (
            _counter(record.get("user_confirmed_count")) >= 1
            and _counter(record.get("process_success_count")) >= self.threshold
            and self._failure_rate(record) <= MAX_FAILURE_RATE
            and record.get("risk_level") != "high"
            and not bool(record.get("scope_review_required"))
        )
        strong = (
            _counter(record.get("verified_success_count")) >= self.threshold
            and _counter(record.get("distinct_document_count")) >= 2
            and record.get("risk_level") != "high"
            and not bool(record.get("scope_review_required"))
        )
        return general, strong

    def _refresh_review_status(self, record):
        record["threshold"] = self.threshold
        record["distinct_document_count"] = len(
            set(record.get("document_fingerprints", []))
        )
        duplicate = self._existing_native_operation(record)
        if duplicate and record.get("status") != "implemented":
            record["status"] = "merged"
            record["merged_with"] = (
                f"{record.get('recommended_implementation_location')}.{duplicate}"
            )
            record["merged_at"] = record.get("merged_at") or _now()
            record["review_reason"] = "existing_native_action"
            record["macro_sufficiency"] = "insufficient"

        general, strong = self._readiness(record)
        record["general_candidate"] = general
        record["strong_candidate"] = strong
        if record.get("status") not in {
            "accepted", "rejected", "implemented", "merged"
        }:
            if general or strong:
                record["status"] = "ready_for_review"
                record["ready_at"] = record.get("ready_at") or _now()
            else:
                record["status"] = "observing"
                record["ready_at"] = ""

    def _update_metadata(self, record, learning, description, selected_route):
        schema = _merge_slot_schemas(
            record.get("slot_schema", []), _slot_schema(learning)
        )
        record["slot_schema"] = schema
        record["required_slots"] = [
            item for item in schema if item.get("required", True)
        ]
        record["selected_route"] = selected_route
        record["description"] = _safe_identifier(
            record.get("normalized_intent")
        ).replace("_", " ")
        templates = list(record.setdefault("utterance_templates", []))
        for item in _utterance_templates(learning):
            if item not in templates:
                templates.append(item)
        record["utterance_templates"] = templates[:5]
        characteristics = _candidate_characteristics(
            record.get("normalized_intent"), record.get("target_app")
        )
        record.update(characteristics)

    @staticmethod
    def _document_fingerprint(signature, document_id):
        value = str(document_id or "").strip()
        if not value:
            return ""
        return hashlib.sha256(f"{signature}|{value}".encode("utf-8")).hexdigest()

    def record_success(
        self, app, learning, description="", *, source="learned_dynamic",
        execution_id="", user_confirmed=False, verified=False,
        observation_kind="user", selected_route="python", document_id="",
    ):
        selected_route = _normalize_route(selected_route)
        # Stage 7/11 invariant: only an actually selected Python route is a
        # dynamic-to-native observation.
        if selected_route != "python":
            return None
        signature = self.signature_for(app, learning)
        if not signature:
            return None
        evidence_id = _bounded_text(execution_id, 100) or uuid.uuid4().hex
        with self._lock:
            record = self._candidates.get(signature)
            if not isinstance(record, dict):
                record = self._base_record(
                    signature, app, learning, description, selected_route
                )
                self._candidates[signature] = record
            self._update_metadata(record, learning, description, selected_route)
            evidence = record.setdefault("evidence_ids", [])
            if evidence_id in evidence:
                return self._public_record(record)
            evidence.append(evidence_id)
            del evidence[:-100]
            if observation_kind == "test":
                record["test_process_success_count"] = _counter(
                    record.get("test_process_success_count")
                ) + 1
                if verified:
                    record["test_verified_success_count"] = _counter(
                        record.get("test_verified_success_count")
                    ) + 1
                record["last_seen_at"] = _now()
                self._refresh_review_status(record)
                self._save()
                return self._public_record(record)

            record["process_success_count"] = _counter(
                record.get("process_success_count")
            ) + 1
            record["consecutive_process_success"] = _counter(
                record.get("consecutive_process_success")
            ) + 1
            record["user_observation_count"] = _counter(
                record.get("user_observation_count")
            ) + 1
            if verified:
                record["verified_success_count"] = _counter(
                    record.get("verified_success_count")
                ) + 1
                record["consecutive_verified_success"] = _counter(
                    record.get("consecutive_verified_success")
                ) + 1
            else:
                record["consecutive_verified_success"] = 0
            if user_confirmed:
                record["user_confirmed_count"] = _counter(
                    record.get("user_confirmed_count")
                ) + 1
            document_fingerprint = self._document_fingerprint(signature, document_id)
            documents = record.setdefault("document_fingerprints", [])
            if document_fingerprint and document_fingerprint not in documents:
                documents.append(document_fingerprint)
                del documents[:-100]
            record["last_seen_at"] = _now()
            safe_source = _bounded_text(source, 40)
            sources = record.setdefault("sources", [])
            if safe_source and safe_source not in sources:
                sources.append(safe_source)
            self._refresh_review_status(record)
            self._save()
            return self._public_record(record)

    def record_failure(
        self, app, learning, *, source="learned_dynamic", execution_id="",
        observation_kind="user", selected_route="python",
    ):
        selected_route = _normalize_route(selected_route)
        if selected_route != "python":
            return None
        signature = self.signature_for(app, learning)
        if not signature:
            return None
        evidence_id = _bounded_text(execution_id, 100) or uuid.uuid4().hex
        with self._lock:
            record = self._candidates.get(signature)
            if not isinstance(record, dict):
                return None
            evidence = record.setdefault("evidence_ids", [])
            if evidence_id in evidence:
                return self._public_record(record)
            evidence.append(evidence_id)
            del evidence[:-100]
            record["selected_route"] = selected_route
            if observation_kind == "test":
                record["test_failure_count"] = _counter(
                    record.get("test_failure_count")
                ) + 1
                record["last_seen_at"] = _now()
                self._save()
                return self._public_record(record)
            record["failure_count"] = _counter(record.get("failure_count")) + 1
            record["user_observation_count"] = _counter(
                record.get("user_observation_count")
            ) + 1
            record["consecutive_process_success"] = 0
            record["consecutive_verified_success"] = 0
            record["last_seen_at"] = _now()
            safe_source = _bounded_text(source, 40)
            sources = record.setdefault("sources", [])
            if safe_source and safe_source not in sources:
                sources.append(safe_source)
            self._refresh_review_status(record)
            self._save()
            return self._public_record(record)

    def _native_recommendation(self, record):
        if record.get("scope_review_required"):
            return "split_scope"
        if record.get("status") == "merged":
            return "already_native"
        if record.get("macro_sufficiency") == "sufficient":
            return "macro_sufficient"
        if record.get("risk_level") == "high":
            return "not_recommended"
        if record.get("strong_candidate"):
            return "strongly_recommended"
        if record.get("status") in {"ready_for_review", "accepted"}:
            return "recommended"
        return "observe"

    def _public_record(self, record):
        public = dict(record)
        for key in ("signature", "evidence_ids", "document_fingerprints"):
            public.pop(key, None)
        public["remaining_process_successes"] = max(
            0, self.threshold - _counter(public.get("process_success_count"))
        )
        public["failure_rate"] = self._failure_rate(public)
        public["high_verification"] = (
            _counter(public.get("verified_success_count")) >= self.threshold
        )
        public["verification_available"] = bool(public.get("verification_hint"))
        public["native_recommendation"] = self._native_recommendation(public)
        public["automatic_execution"] = False
        public["automatic_code_generation"] = False
        return public

    def list_candidates(self, include_observing=True, include_dismissed=False):
        """Return public records; include_dismissed now means all closed states."""
        with self._lock:
            records = []
            for record in self._candidates.values():
                if not isinstance(record, dict):
                    continue
                status = record.get("status", "observing")
                if status in CLOSED_STATUSES and not include_dismissed:
                    continue
                if status == "observing" and not include_observing:
                    continue
                records.append(self._public_record(record))
            priority = {
                "ready_for_review": 0, "accepted": 1, "observing": 2,
                "rejected": 3, "implemented": 4, "merged": 5,
            }
            return sorted(
                records,
                key=lambda item: (
                    priority.get(item.get("status"), 9),
                    not bool(item.get("strong_candidate")),
                    str(item.get("last_seen_at", "")),
                ),
            )

    def _find(self, candidate_id):
        candidate_id = _bounded_text(candidate_id, 32)
        return next(
            (
                item for item in self._candidates.values()
                if isinstance(item, dict) and item.get("candidate_id") == candidate_id
            ),
            None,
        )

    def _implementation_spec(self, record):
        return {
            "candidate_id": record["candidate_id"],
            "task_name": record["suggested_native_action_name"],
            "target_app": record["target_app"],
            "required_slots": [dict(item) for item in record.get("required_slots", [])],
            "selected_route": record.get("selected_route", "python"),
            "risk_level": record.get("risk_level", "low"),
            "change_type": record.get("change_type", "local_action"),
            "verification_method": record.get("verification_hint", ""),
            "rollback_required": bool(record.get("rollback_required", False)),
            "recommended_implementation_location": record.get(
                "recommended_implementation_location", "ActionExecutor"
            ),
            "generated_at": _now(),
            "code_generated": False,
        }

    def set_status(self, candidate_id, status, reason=""):
        normalized = str(status or "").strip().casefold()
        if normalized == "dismissed":
            normalized = "rejected"
            reason = reason or "legacy_dismissed"
        if normalized not in {
            "observing", "accepted", "rejected", "implemented", "merged"
        }:
            raise ValueError(
                "확장 후보 상태는 observing, accepted, rejected, implemented, merged 중 하나여야 합니다."
            )
        safe_reason = _reason_identifier(reason)
        with self._lock:
            record = self._find(candidate_id)
            if record is None:
                raise ValueError("확장 후보를 찾지 못했습니다.")
            previous = record.get("status", "observing")
            self._refresh_review_status(record)
            if normalized == "accepted":
                if record.get("status") != "ready_for_review":
                    raise ValueError("검토 준비 조건을 충족한 후보만 승인할 수 있습니다.")
                record["status"] = "accepted"
                record["accepted_at"] = _now()
                record["review_reason"] = safe_reason or "user_approved"
                record["macro_sufficiency"] = "insufficient"
                record["implementation_spec"] = self._implementation_spec(record)
            elif normalized == "implemented":
                if previous != "accepted":
                    raise ValueError("승인된 후보만 구현 완료로 변경할 수 있습니다.")
                record["status"] = "implemented"
                record["implemented_at"] = _now()
                record["review_reason"] = safe_reason or "implementation_completed"
            elif normalized == "merged":
                record["status"] = "merged"
                record["merged_at"] = _now()
                record["review_reason"] = safe_reason or "manual_native_merge"
                record["macro_sufficiency"] = "insufficient"
            elif normalized == "rejected":
                record["status"] = "rejected"
                record["rejected_at"] = _now()
                record["review_reason"] = safe_reason or "user_rejected"
                record["macro_sufficiency"] = (
                    "sufficient" if safe_reason == "macro_sufficient"
                    else "undetermined"
                )
            else:
                record["status"] = "observing"
                record["review_reason"] = safe_reason or "review_deferred"
                record["macro_sufficiency"] = "undetermined"
                record["implementation_spec"] = {}
            # An exact existing native action is always a merge, even when a
            # stale client asks to reopen or reject the duplicate candidate.
            if (
                self._existing_native_operation(record)
                and record.get("status") != "implemented"
            ):
                self._refresh_review_status(record)
            record.setdefault("status_history", []).append({
                "previous_status": previous,
                "new_status": record["status"],
                "changed_at": _now(),
                "reason": record.get("review_reason", ""),
            })
            del record["status_history"][:-30]
            self._save()
            return self._public_record(record)

    def get_implementation_spec(self, candidate_id):
        with self._lock:
            record = self._find(candidate_id)
            if record is None:
                raise ValueError("확장 후보를 찾지 못했습니다.")
            if record.get("status") not in {"accepted", "implemented"}:
                raise ValueError("승인된 후보만 구현 명세를 볼 수 있습니다.")
            if not record.get("implementation_spec"):
                record["implementation_spec"] = self._implementation_spec(record)
                self._save()
            return dict(record["implementation_spec"])
