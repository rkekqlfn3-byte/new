"""Deterministic, privacy-bounded ownership triage for completed outcomes.

This module is deliberately observational.  It never executes a retry, changes
an application, relaxes a policy, or modifies source code.  Existing execution
boundaries remain the only owners of those decisions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json


TRIAGE_SCHEMA_VERSION = 1
DEVELOPER_ISSUE_SCHEMA_VERSION = 1
DEFAULT_DEVELOPER_ISSUE_PATH = user_data_path("developer_issues.json")
DEVELOPER_ISSUE_STATUSES = frozenset({
    "new", "acknowledged", "resolved", "dismissed",
})

_CATEGORY_USER_MESSAGES = {
    "runtime_recoverable": (
        "현재 기능 안에서 다시 해석하거나 대상을 탐색하면 해결될 수 있습니다. "
        "자동 재실행은 하지 않았으며 안전한 재시도 가능 여부만 표시합니다."
    ),
    "needs_input": (
        "가능한 대상이나 해석이 여러 개라 사용자 선택이 필요합니다. "
        "한 번에 한 가지 정보만 확인한 뒤 이어서 실행할 수 있습니다."
    ),
    "missing_capability": (
        "요청과 대상은 이해했지만 현재 JARVIS에 필요한 기능이 없습니다. "
        "개발 항목으로 기록했습니다."
    ),
    "implementation_bug": (
        "지원되는 작업이지만 실행 또는 결과 검증에 실패했습니다. "
        "추가 실행은 멈추고 익명화된 재현 항목으로 기록했습니다."
    ),
    "environment_blocked": (
        "기능은 지원하지만 현재 앱·문서·권한 상태가 실행을 막았습니다. "
        "차단 조건을 해결한 뒤 같은 요청을 다시 시도할 수 있습니다."
    ),
    "policy_blocked": (
        "안전 정책상 이 작업은 실행하지 않았습니다. 차단 이유를 유지한 "
        "안전한 대안이 있는지 확인해 주세요."
    ),
    "preference_mismatch": (
        "작업과 검증은 완료됐지만 원하는 형식과 달랐습니다. "
        "수정 방향을 알려주면 다음 작업의 선호 후보로 반영할 수 있습니다."
    ),
    "unknown": (
        "자동 진단에 필요한 증거가 부족합니다. 추가 정보를 기록하고 "
        "사용자 또는 개발자가 확인해야 합니다."
    ),
}


class FailureCategory(str, Enum):
    RUNTIME_RECOVERABLE = "runtime_recoverable"
    NEEDS_INPUT = "needs_input"
    MISSING_CAPABILITY = "missing_capability"
    IMPLEMENTATION_BUG = "implementation_bug"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    POLICY_BLOCKED = "policy_blocked"
    PREFERENCE_MISMATCH = "preference_mismatch"
    UNKNOWN = "unknown"


class FailureOwner(str, Enum):
    JARVIS = "jarvis"
    USER = "user"
    DEVELOPER = "developer"
    ENVIRONMENT = "environment"
    POLICY = "policy"
    UNKNOWN = "unknown"


class EvidenceCode(str, Enum):
    INTENT_NOT_RESOLVED = "INTENT_NOT_RESOLVED"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    MULTIPLE_TARGETS = "MULTIPLE_TARGETS"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_DISCOVERY_EXHAUSTED = "TARGET_DISCOVERY_EXHAUSTED"
    SAFE_PREEXECUTION_RECOVERY = "SAFE_PREEXECUTION_RECOVERY"
    RECOVERY_TARGET_CHANGED = "RECOVERY_TARGET_CHANGED"
    RECOVERY_AFTER_EXECUTION_BLOCKED = "RECOVERY_AFTER_EXECUTION_BLOCKED"
    RECOVERY_RETRY_EXHAUSTED = "RECOVERY_RETRY_EXHAUSTED"
    OPERATION_NOT_REGISTERED = "OPERATION_NOT_REGISTERED"
    ADAPTER_NOT_AVAILABLE = "ADAPTER_NOT_AVAILABLE"
    APP_NOT_INSTALLED = "APP_NOT_INSTALLED"
    DOCUMENT_READ_ONLY = "DOCUMENT_READ_ONLY"
    DOCUMENT_PROTECTED = "DOCUMENT_PROTECTED"
    OFFICE_BUSY = "OFFICE_BUSY"
    CONTEXT_CHANGED = "CONTEXT_CHANGED"
    TIMEOUT = "TIMEOUT"
    EXECUTION_EXCEPTION = "EXECUTION_EXCEPTION"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    USER_REJECTED_VERIFIED_RESULT = "USER_REJECTED_VERIFIED_RESULT"
    POLICY_DENIED = "POLICY_DENIED"
    RETRYABLE_RUNTIME = "RETRYABLE_RUNTIME"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class FailureTriageResult:
    triage_id: str
    session_id: str | None
    request_id: str | None
    category: str
    confidence: float
    classification_source: str
    owner: str
    next_action: str
    intent_understood: bool | None
    target_resolved: bool | None
    capability_exists: bool | None
    execution_started: bool
    execution_succeeded: bool | None
    verification_succeeded: bool | None
    rollback_succeeded: bool | None
    evidence_codes: list[str]
    user_message: str
    developer_summary: str | None
    retry_allowed: bool
    retry_count: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = TRIAGE_SCHEMA_VERSION
        return value


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _digest(value) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()


def _safe_identifier(value, limit=100) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(rf"[a-zA-Z0-9_.:-]{{1,{int(limit)}}}", text):
        return text
    return f"anon_{_digest(text)[:12].casefold()}"


def _safe_hash(value) -> str:
    text = str(value or "")
    return text.upper() if re.fullmatch(r"[A-Fa-f0-9]{64}", text) else _digest(text)


def _safe_timestamp(value) -> str | None:
    try:
        return datetime.fromisoformat(str(value or "")).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def _mapping(value) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_bool(*values) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _bounded_int(value, default=0, maximum=2) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(number, maximum))


def _result_parts(record: Mapping[str, Any]):
    result = _mapping(record.get("result"))
    data = _mapping(result.get("data"))
    diagnostic = _mapping(data.get("diagnostic_context"))
    hints = _mapping(data.get("triage_hints"))
    return result, data, diagnostic, hints


def _combined_text(record: Mapping[str, Any]) -> str:
    result, data, diagnostic, hints = _result_parts(record)
    safe_values = (
        record.get("error"),
        record.get("response"),
        result.get("message"),
        result.get("status"),
        result.get("error_type"),
        data.get("reason"),
        diagnostic.get("reason"),
        hints.get("reason"),
    )
    return " ".join(str(value or "") for value in safe_values).casefold()


def _contains_any(text: str, terms) -> bool:
    return any(term in text for term in terms)


def _event_evidence(record: Mapping[str, Any]):
    execution_started = False
    rollback_performed = False
    rollback_succeeded = None
    verification_failed = False
    for event in list(record.get("events") or []):
        if not isinstance(event, Mapping):
            continue
        action = str(event.get("action") or "").casefold()
        status = str(event.get("status") or "").casefold()
        details = _mapping(event.get("details"))
        if any(token in action for token in ("execute", "execution", "native", "adapter")):
            if status not in {"pending", "confirmation_required"}:
                execution_started = True
        if "verification" in action or "verification" in status:
            verification_failed = verification_failed or status in {
                "failed", "mismatch", "verification_failed",
            }
        if details.get("rollback_performed") is True:
            rollback_performed = True
            rollback_succeeded = _optional_bool(details.get("rollback_success"))
    return execution_started, rollback_performed, rollback_succeeded, verification_failed


def unknown_triage(*, created_at=None) -> dict[str, Any]:
    return FailureTriageResult(
        triage_id=uuid.uuid4().hex,
        session_id=None,
        request_id=None,
        category=FailureCategory.UNKNOWN.value,
        confidence=0.0,
        classification_source="deterministic",
        owner=FailureOwner.UNKNOWN.value,
        next_action="collect_more_evidence",
        intent_understood=None,
        target_resolved=None,
        capability_exists=None,
        execution_started=False,
        execution_succeeded=None,
        verification_succeeded=None,
        rollback_succeeded=None,
        evidence_codes=[EvidenceCode.INSUFFICIENT_EVIDENCE.value],
        user_message=(
            "자동 진단에 필요한 증거가 부족합니다. 추가 정보를 기록하고 "
            "사용자 또는 개발자가 확인해야 합니다."
        ),
        developer_summary="category=unknown evidence=INSUFFICIENT_EVIDENCE",
        retry_allowed=False,
        retry_count=0,
        created_at=str(created_at or _timestamp()),
    ).to_dict()


def normalize_triage(value) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    categories = {item.value for item in FailureCategory}
    owners = {item.value for item in FailureOwner}
    evidence_values = {item.value for item in EvidenceCode}
    if str(raw.get("category") or "") not in categories:
        return unknown_triage(created_at=raw.get("created_at"))
    normalized = unknown_triage(created_at=raw.get("created_at"))
    normalized.update({
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "triage_id": (
            str(raw.get("triage_id"))
            if re.fullmatch(r"[a-f0-9]{32}", str(raw.get("triage_id") or ""))
            else uuid.uuid4().hex
        ),
        "session_id": _safe_identifier(raw.get("session_id")),
        "request_id": _safe_identifier(raw.get("request_id")),
        "category": str(raw["category"]),
        "owner": (
            str(raw.get("owner"))
            if str(raw.get("owner") or "") in owners
            else FailureOwner.UNKNOWN.value
        ),
        "classification_source": "deterministic",
        "next_action": _safe_identifier(raw.get("next_action")) or "collect_more_evidence",
        "evidence_codes": sorted({
            str(item) for item in list(raw.get("evidence_codes") or [])
            if str(item) in evidence_values
        }) or [EvidenceCode.INSUFFICIENT_EVIDENCE.value],
        # Never trust persisted/free-form messages here. Recreate both fields
        # exclusively from enum values so diagnostics cannot retain content.
        "user_message": _CATEGORY_USER_MESSAGES[str(raw["category"])],
        "developer_summary": (
            f"category={str(raw['category'])} "
            f"owner={str(raw.get('owner')) if str(raw.get('owner') or '') in owners else FailureOwner.UNKNOWN.value} "
            "evidence="
            + ",".join(sorted({
                str(item) for item in list(raw.get("evidence_codes") or [])
                if str(item) in evidence_values
            }) or [EvidenceCode.INSUFFICIENT_EVIDENCE.value])
        ),
        "retry_allowed": bool(raw.get("retry_allowed", False)),
        "retry_count": _bounded_int(raw.get("retry_count"), maximum=2),
    })
    try:
        normalized["confidence"] = max(0.0, min(float(raw.get("confidence", 0)), 1.0))
    except (TypeError, ValueError):
        normalized["confidence"] = 0.0
    for key in (
        "intent_understood", "target_resolved", "capability_exists",
        "execution_succeeded", "verification_succeeded", "rollback_succeeded",
    ):
        normalized[key] = raw.get(key) if isinstance(raw.get(key), bool) else None
    normalized["execution_started"] = bool(raw.get("execution_started", False))
    return normalized


class DeterministicFailureClassifier:
    """Classify completed outcomes using explicit state and execution evidence."""

    _POLICY_TERMS = (
        "policy_denied", "policy denied", "보안상 허용하지", "보안상 차단",
        "직접 명령 실행은", "승인 없는", "고위험", "unsafe launch",
        "보호 기능 강제", "잠긴 vba", "risk policy",
    )
    _ENVIRONMENT_TERMS = (
        "access is denied", "permission denied", "read-only", "readonly",
        "읽기 전용", "protected document", "문서가 보호", "시트가 보호",
        "trust center", "programmatic access", "rpc_e_call_rejected",
        "servercall_retrylater", "call was rejected", "office busy",
        "class not registered", "app not installed", "앱이 설치되지",
        "파일 잠금", "locked file", "관리자 정책",
    )
    _MISSING_TERMS = (
        "operation_not_registered", "operation not registered",
        "unsupported operation", "not implemented", "기능이 없습니다",
        "기능이 아직 없습니다", "아직 지원하지 않는", "지원하지 않는 작업",
        "adapter not available", "어댑터가 없습니다", "어댑터 없음",
    )
    _AMBIGUOUS_TERMS = (
        "ambiguous", "multiple targets", "multiple candidates", "여러 후보",
        "같은 이름", "대상이 여러", "하나를 선택", "선택해주세요",
        "의미가 여러", "모호",
    )
    _INTENT_TERMS = (
        "intent not resolved", "의도를 이해하지", "명령을 이해하지",
        "요청을 이해하지", "무슨 뜻인지",
    )

    def classify(self, record: Mapping[str, Any]) -> FailureTriageResult:
        record = record if isinstance(record, Mapping) else {}
        result, data, diagnostic, hints = _result_parts(record)
        text = _combined_text(record)
        error_type = str(
            record.get("error_type") or result.get("error_type") or ""
        ).casefold()
        status = str(record.get("status") or result.get("status") or "").casefold()
        action = str(result.get("action") or "").casefold()
        operation = data.get("operation") or diagnostic.get("operation")
        recovery = _mapping(data.get("automatic_recovery"))
        retry_count = _bounded_int(
            data.get("retry_count", record.get("retry_count", 0)), maximum=2
        )
        recovery_retry_limit = _bounded_int(
            recovery.get("retry_limit"), default=0, maximum=2
        )
        recovery_contract_valid = bool(
            recovery.get("schema_version") == 1
            and recovery.get("phase") == "pre_execution"
            and recovery.get("execution_started") is False
            and recovery.get("target_unchanged") in {True, False}
            and recovery_retry_limit == 1
            and 0 <= retry_count <= 1
            and re.fullmatch(
                r"[A-F0-9]{64}",
                str(recovery.get("target_signature") or "").upper(),
            )
        )
        recovery_target_changed = bool(
            recovery.get("outcome") == "target_changed"
            or hints.get("recovery_target_changed") is True
        )
        recovery_after_execution = bool(
            recovery.get("outcome") == "blocked_after_execution"
            or recovery.get("execution_started") is True
            or hints.get("recovery_after_execution_blocked") is True
        )
        recovery_retry_exhausted = recovery.get("outcome") == "retry_exhausted"
        discovery_exhausted = bool(
            recovery.get("attempted") is True
            and recovery.get("strategy") == "bounded_app_rediscovery"
            and recovery.get("outcome") == "not_found"
            and retry_count >= 1
        )
        retryable = bool(record.get("retryable", result.get("retryable", False)))
        success = bool(record.get("success", result.get("success", False)))
        verified = bool(record.get("verified", result.get("verified", False)))
        explicit_intent = _optional_bool(
            hints.get("intent_understood"), data.get("intent_understood"),
            diagnostic.get("intent_understood"),
        )
        explicit_target = _optional_bool(
            hints.get("target_resolved"), data.get("target_resolved"),
            diagnostic.get("target_resolved"),
        )
        explicit_capability = _optional_bool(
            hints.get("capability_exists"), data.get("capability_exists"),
            diagnostic.get("capability_exists"),
        )
        policy_denied = any(value is True for value in (
            hints.get("policy_denied"), data.get("policy_denied"),
            diagnostic.get("policy_denied"),
        )) or _contains_any(text, self._POLICY_TERMS)
        user_rejected = any(value is True for value in (
            hints.get("user_rejected_verified_result"),
            data.get("user_rejected_verified_result"),
            diagnostic.get("user_rejected_verified_result"),
        ))
        event_started, rollback_performed, rollback_succeeded, event_verify_failed = (
            _event_evidence(record)
        )
        execution_started = bool(
            event_started
            or recovery_after_execution
            or operation
            or error_type in {"execution_error", "verification_error", "timeout"}
            or result.get("failed_step") not in (None, "", -1)
            or record.get("failed_step") not in (None, "", -1)
        )
        if not rollback_performed:
            rollback_performed = bool(
                diagnostic.get("rollback_performed")
                or data.get("rollback_performed")
            )
            rollback_succeeded = _optional_bool(
                diagnostic.get("rollback_success"), data.get("rollback_success")
            ) if rollback_performed else None
        verification_failed = bool(
            error_type == "verification_error"
            or "verification" in status
            or event_verify_failed
            or diagnostic.get("verification_status") in {"failed", "mismatch"}
        )
        multiple_targets = _contains_any(text, self._AMBIGUOUS_TERMS) or any(
            value is True for value in (
                hints.get("multiple_targets"), data.get("multiple_targets"),
                diagnostic.get("multiple_targets"),
            )
        )
        target_not_found = error_type == "target_not_found" or status in {
            "not_found", "target_not_found",
        }
        context_changed = status in {"context_changed", "stale_context"} or (
            "fingerprint" in text and "바뀌" in text
        )
        office_busy = error_type == "busy" or _contains_any(text, (
            "rpc_e_call_rejected", "servercall_retrylater", "call was rejected",
            "office busy", "호출이 거부",
        ))
        timed_out = error_type == "timeout" or "timed out" in text or (
            "시간" in text and "초과" in text
        )
        app_not_installed = _contains_any(text, (
            "app not installed", "앱이 설치되지", "class not registered",
            "설치된 프로그램을 찾", "no module named",
        ))
        read_only = _contains_any(text, ("read-only", "readonly", "읽기 전용"))
        protected = _contains_any(text, (
            "protected document", "문서가 보호", "시트가 보호", "보호된 문서",
        ))
        environment_blocked = (
            error_type in {"environment_error", "busy"}
            or context_changed or office_busy or app_not_installed or read_only
            or protected or _contains_any(text, self._ENVIRONMENT_TERMS)
            or any(value is True for value in (
                hints.get("environment_blocked"), data.get("environment_blocked"),
                diagnostic.get("environment_blocked"),
            ))
        )
        capability_missing = explicit_capability is False or _contains_any(
            text, self._MISSING_TERMS
        )
        intent_not_resolved = explicit_intent is False or _contains_any(
            text, self._INTENT_TERMS
        )

        evidence = []
        if recovery_contract_valid:
            evidence.append(EvidenceCode.SAFE_PREEXECUTION_RECOVERY.value)
        if recovery_target_changed:
            evidence.append(EvidenceCode.RECOVERY_TARGET_CHANGED.value)
        if recovery_after_execution:
            evidence.append(EvidenceCode.RECOVERY_AFTER_EXECUTION_BLOCKED.value)
        if recovery_retry_exhausted:
            evidence.append(EvidenceCode.RECOVERY_RETRY_EXHAUSTED.value)
        if intent_not_resolved:
            evidence.append(EvidenceCode.INTENT_NOT_RESOLVED.value)
        if multiple_targets:
            evidence.extend((
                EvidenceCode.AMBIGUOUS_REQUEST.value,
                EvidenceCode.MULTIPLE_TARGETS.value,
            ))
        if target_not_found:
            evidence.append(EvidenceCode.TARGET_NOT_FOUND.value)
        if discovery_exhausted:
            evidence.append(EvidenceCode.TARGET_DISCOVERY_EXHAUSTED.value)
        if capability_missing:
            evidence.append(EvidenceCode.OPERATION_NOT_REGISTERED.value)
        if "adapter not available" in text or "어댑터" in text and "없" in text:
            evidence.append(EvidenceCode.ADAPTER_NOT_AVAILABLE.value)
        if app_not_installed:
            evidence.append(EvidenceCode.APP_NOT_INSTALLED.value)
        if read_only:
            evidence.append(EvidenceCode.DOCUMENT_READ_ONLY.value)
        if protected:
            evidence.append(EvidenceCode.DOCUMENT_PROTECTED.value)
        if office_busy:
            evidence.append(EvidenceCode.OFFICE_BUSY.value)
        if context_changed:
            evidence.append(EvidenceCode.CONTEXT_CHANGED.value)
        if timed_out:
            evidence.append(EvidenceCode.TIMEOUT.value)
        if error_type == "execution_error" or execution_started and not success:
            evidence.append(EvidenceCode.EXECUTION_EXCEPTION.value)
        if verification_failed:
            evidence.append(EvidenceCode.POSTCONDITION_FAILED.value)
        if rollback_performed and rollback_succeeded is False:
            evidence.append(EvidenceCode.ROLLBACK_FAILED.value)
        if user_rejected and success and verified:
            evidence.append(EvidenceCode.USER_REJECTED_VERIFIED_RESULT.value)
        if policy_denied:
            evidence.append(EvidenceCode.POLICY_DENIED.value)
        if retryable:
            evidence.append(EvidenceCode.RETRYABLE_RUNTIME.value)

        if user_rejected and success and verified:
            category = FailureCategory.PREFERENCE_MISMATCH
            owner = FailureOwner.USER
            next_action = "request_preference_correction"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif policy_denied:
            category = FailureCategory.POLICY_BLOCKED
            owner = FailureOwner.POLICY
            next_action = "offer_safe_alternative"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif recovery_after_execution:
            category = FailureCategory.IMPLEMENTATION_BUG
            owner = FailureOwner.DEVELOPER
            next_action = "register_unsafe_recovery_attempt"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif recovery_target_changed or recovery_retry_exhausted:
            category = FailureCategory.NEEDS_INPUT
            owner = FailureOwner.USER
            next_action = "ask_for_app_name_or_location"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif multiple_targets:
            category = FailureCategory.NEEDS_INPUT
            owner = FailureOwner.USER
            next_action = "ask_user_once"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif discovery_exhausted:
            category = FailureCategory.NEEDS_INPUT
            owner = FailureOwner.USER
            next_action = "ask_for_app_name_or_location"
            confidence = 0.98
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif environment_blocked:
            category = FailureCategory.ENVIRONMENT_BLOCKED
            owner = FailureOwner.ENVIRONMENT
            next_action = "explain_environment_and_retry"
            confidence = 0.98
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif capability_missing:
            category = FailureCategory.MISSING_CAPABILITY
            owner = FailureOwner.DEVELOPER
            next_action = "register_development_capability"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif verification_failed or rollback_performed and rollback_succeeded is False:
            category = FailureCategory.IMPLEMENTATION_BUG
            owner = FailureOwner.DEVELOPER
            next_action = "register_bug_and_reproduce"
            confidence = 1.0
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif (
            error_type == "execution_error"
            and execution_started
            and (operation or action not in {"", "command", "unknown"})
        ):
            category = FailureCategory.IMPLEMENTATION_BUG
            owner = FailureOwner.DEVELOPER
            next_action = "register_bug_and_reproduce"
            confidence = 0.9
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        elif intent_not_resolved or target_not_found or retryable or timed_out:
            category = FailureCategory.RUNTIME_RECOVERABLE
            owner = FailureOwner.JARVIS
            next_action = "retry_existing_tools_or_search"
            confidence = 0.85 if not retryable else 0.95
            user_message = _CATEGORY_USER_MESSAGES[category.value]
        else:
            category = FailureCategory.UNKNOWN
            owner = FailureOwner.UNKNOWN
            next_action = "collect_more_evidence"
            confidence = 0.0
            evidence.append(EvidenceCode.INSUFFICIENT_EVIDENCE.value)
            user_message = _CATEGORY_USER_MESSAGES[category.value]

        if category == FailureCategory.MISSING_CAPABILITY:
            intent_understood = True if explicit_intent is None else explicit_intent
            target_resolved = True if explicit_target is None else explicit_target
            capability_exists = False
        elif category == FailureCategory.IMPLEMENTATION_BUG:
            intent_understood = True if explicit_intent is None else explicit_intent
            target_resolved = True if explicit_target is None else explicit_target
            capability_exists = True if explicit_capability is None else explicit_capability
        elif category in {FailureCategory.ENVIRONMENT_BLOCKED, FailureCategory.POLICY_BLOCKED}:
            intent_understood = True if explicit_intent is None else explicit_intent
            target_resolved = explicit_target
            capability_exists = True if explicit_capability is None else explicit_capability
        else:
            intent_understood = (
                False if intent_not_resolved else explicit_intent
            )
            target_resolved = (
                False if target_not_found or multiple_targets else explicit_target
            )
            capability_exists = explicit_capability

        retry_allowed = bool(
            retryable
            and category in {
                FailureCategory.RUNTIME_RECOVERABLE,
                FailureCategory.ENVIRONMENT_BLOCKED,
            }
            and not policy_denied
            and not verification_failed
            and not (rollback_performed and rollback_succeeded is False)
        )
        if success:
            execution_succeeded = True
        elif execution_started:
            execution_succeeded = False
        else:
            execution_succeeded = None
        if verified:
            verification_succeeded = True
        elif verification_failed:
            verification_succeeded = False
        else:
            verification_succeeded = None

        evidence = sorted(set(evidence)) or [EvidenceCode.INSUFFICIENT_EVIDENCE.value]
        metadata = _mapping(record.get("metadata"))
        session_id = _safe_identifier(
            metadata.get("session_id") or metadata.get("edit_session_id")
        )
        request_id = _safe_identifier(
            data.get("request_id") or diagnostic.get("request_id")
        )
        developer_summary = (
            f"category={category.value} owner={owner.value} "
            f"evidence={','.join(evidence)}"
        )
        return FailureTriageResult(
            triage_id=uuid.uuid4().hex,
            session_id=session_id,
            request_id=request_id,
            category=category.value,
            confidence=confidence,
            classification_source="deterministic",
            owner=owner.value,
            next_action=next_action,
            intent_understood=intent_understood,
            target_resolved=target_resolved,
            capability_exists=capability_exists,
            execution_started=execution_started,
            execution_succeeded=execution_succeeded,
            verification_succeeded=verification_succeeded,
            rollback_succeeded=rollback_succeeded if rollback_performed else None,
            evidence_codes=evidence,
            user_message=user_message,
            developer_summary=developer_summary,
            retry_allowed=retry_allowed,
            retry_count=retry_count,
            created_at=_timestamp(),
        )


class DeveloperIssueRegistry:
    """Local, deduplicated queue for capability gaps and implementation bugs."""

    _ALLOWED_CATEGORIES = frozenset({
        FailureCategory.MISSING_CAPABILITY.value,
        FailureCategory.IMPLEMENTATION_BUG.value,
    })

    def __init__(self, path=None, *, max_issues=500):
        self.path = str(path or DEFAULT_DEVELOPER_ISSUE_PATH)
        self.max_issues = max(10, min(int(max_issues), 5_000))
        self._lock = threading.RLock()
        self._data = self._normalize(safe_read_json(self.path, self._default_data()))

    @staticmethod
    def _default_data():
        return {"schema_version": DEVELOPER_ISSUE_SCHEMA_VERSION, "issues": []}

    @classmethod
    def _normalize(cls, value):
        raw = value if isinstance(value, Mapping) else {}
        issues = []
        for item in raw.get("issues", []) if isinstance(raw.get("issues"), list) else []:
            if not isinstance(item, Mapping):
                continue
            issue_id = str(item.get("issue_id") or "")
            fingerprint = str(item.get("fingerprint") or "")
            category = str(item.get("category") or "")
            if not re.fullmatch(r"[a-f0-9]{32}", issue_id):
                continue
            if not re.fullmatch(r"[A-F0-9]{64}", fingerprint):
                continue
            if category not in cls._ALLOWED_CATEGORIES:
                continue
            try:
                frequency = int(item.get("frequency") or 1)
            except (TypeError, ValueError):
                frequency = 1
            signature = _mapping(item.get("signature"))
            extension = str(signature.get("document_extension") or "")
            evidence_values = {evidence.value for evidence in EvidenceCode}
            normalized = {
                "schema_version": DEVELOPER_ISSUE_SCHEMA_VERSION,
                "issue_id": issue_id,
                "fingerprint": fingerprint,
                "status": (
                    str(item.get("status"))
                    if str(item.get("status") or "") in DEVELOPER_ISSUE_STATUSES
                    else "new"
                ),
                "category": category,
                "owner": FailureOwner.DEVELOPER.value,
                "frequency": max(1, frequency),
                "first_seen_at": _safe_timestamp(item.get("first_seen_at")),
                "last_seen_at": _safe_timestamp(item.get("last_seen_at")),
                "last_incident_id": _safe_identifier(item.get("last_incident_id"), 32),
                "request_hash": _safe_hash(item.get("request_hash")),
                "last_error_signature": _safe_hash(item.get("last_error_signature")),
                "signature": {
                    "category": category,
                    "app_type": _safe_identifier(signature.get("app_type")),
                    "operation": _safe_identifier(signature.get("operation")),
                    "action": _safe_identifier(signature.get("action")),
                    "document_extension": (
                        extension.casefold()
                        if re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", extension)
                        else None
                    ),
                    "error_type": _safe_identifier(signature.get("error_type")),
                    "failed_step": _safe_identifier(signature.get("failed_step")),
                    "evidence_codes": sorted({
                        str(code) for code in list(
                            signature.get("evidence_codes") or []
                        ) if str(code) in evidence_values
                    }),
                },
                "next_action": _safe_identifier(item.get("next_action"))
                or "register_bug_and_reproduce",
                "raw_content_stored": False,
                "external_issue_created": False,
                "automatic_code_change": False,
            }
            issues.append(normalized)
        return {"schema_version": DEVELOPER_ISSUE_SCHEMA_VERSION, "issues": issues}

    def _save_locked(self):
        atomic_write_json(self.path, self._data, max_versions=3)

    def record(self, triage, *, incident, interpreted, error_signature, request_hash):
        triage = normalize_triage(triage)
        category = triage["category"]
        if category not in self._ALLOWED_CATEGORIES:
            return None
        interpreted = interpreted if isinstance(interpreted, Mapping) else {}
        signature = {
            "category": category,
            "app_type": _safe_identifier(interpreted.get("app_type")),
            "operation": _safe_identifier(interpreted.get("operation")),
            "action": _safe_identifier(interpreted.get("action")),
            "document_extension": (
                str(interpreted.get("document_extension") or "").casefold()
                if re.fullmatch(
                    r"\.[a-zA-Z0-9]{1,8}",
                    str(interpreted.get("document_extension") or ""),
                ) else None
            ),
            "error_type": _safe_identifier(interpreted.get("error_type")),
            "failed_step": _safe_identifier(interpreted.get("failed_step")),
            "evidence_codes": list(triage["evidence_codes"]),
        }
        fingerprint = _digest(json.dumps(
            signature, sort_keys=True, separators=(",", ":")
        ))
        now = _timestamp()
        with self._lock:
            existing = next((
                item for item in self._data["issues"]
                if item.get("fingerprint") == fingerprint
            ), None)
            if existing is not None:
                existing["frequency"] = int(existing.get("frequency") or 1) + 1
                existing["last_seen_at"] = now
                incident_id = str(_mapping(incident).get("incident_id") or "")
                existing["last_incident_id"] = (
                    incident_id if re.fullmatch(r"[a-f0-9]{32}", incident_id)
                    else _digest(incident_id)[:32].casefold()
                )
                existing["last_error_signature"] = _safe_hash(error_signature)
                if existing.get("status") == "resolved":
                    existing["status"] = "new"
                self._save_locked()
                return copy.deepcopy(existing)
            issue = {
                "schema_version": DEVELOPER_ISSUE_SCHEMA_VERSION,
                "issue_id": uuid.uuid4().hex,
                "fingerprint": fingerprint,
                "status": "new",
                "category": category,
                "owner": FailureOwner.DEVELOPER.value,
                "frequency": 1,
                "first_seen_at": now,
                "last_seen_at": now,
                "last_incident_id": (
                    str(_mapping(incident).get("incident_id"))
                    if re.fullmatch(
                        r"[a-f0-9]{32}",
                        str(_mapping(incident).get("incident_id") or ""),
                    )
                    else _digest(_mapping(incident).get("incident_id"))[:32].casefold()
                ),
                "request_hash": _safe_hash(request_hash),
                "last_error_signature": _safe_hash(error_signature),
                "signature": signature,
                "next_action": triage["next_action"],
                "raw_content_stored": False,
                "external_issue_created": False,
                "automatic_code_change": False,
            }
            self._data["issues"].append(issue)
            self._data["issues"] = self._data["issues"][-self.max_issues:]
            self._save_locked()
            return copy.deepcopy(issue)

    def list_issues(self, limit=50, *, status=None):
        limit = max(1, min(int(limit or 50), 500))
        if status is not None and str(status) not in DEVELOPER_ISSUE_STATUSES:
            raise ValueError("개발 이슈 상태가 올바르지 않습니다.")
        with self._lock:
            values = [
                copy.deepcopy(item) for item in reversed(self._data["issues"])
                if status is None or item.get("status") == status
            ]
        return values[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._data["issues"])
