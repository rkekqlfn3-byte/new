"""Canonical command execution results shared by every execution boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ERROR_TYPES = frozenset({
    "contract_error",
    "validation_error",
    "verification_error",
    "execution_error",
    "timeout",
    "target_not_found",
    "user_cancelled",
    "environment_error",
    "busy",
    "unknown",
})

ERROR_TYPE_ALIASES = {
    "cancelled": "user_cancelled",
    "canceled": "user_cancelled",
    "user_rejected": "user_cancelled",
}

NON_FAILURE_STATUSES = frozenset({
    "confirmation_required", "clarification_required",
})

FAILURE_STATUSES = frozenset({
    "blocked", "cancelled", "failed", "verification_failed",
})

EXECUTION_RESULT_FIELDS = frozenset({
    "success",
    "message",
    "action",
    "target",
    "verified",
    "status",
    "error_type",
    "failed_step",
    "retryable",
    "data",
})

__all__ = [
    "ERROR_TYPES",
    "ERROR_TYPE_ALIASES",
    "NON_FAILURE_STATUSES",
    "FAILURE_STATUSES",
    "EXECUTION_RESULT_FIELDS",
    "ExecutionResult",
    "ExecutionResultDict",
    "confirmation_result",
    "clarification_result",
    "execution_result_contract_errors",
    "failure_result",
    "is_execution_result",
    "normalize_error_type",
    "normalize_execution_result",
    "result_message",
    "success_result",
]


class ExecutionResultDict(dict):
    """Dict result with message-friendly compatibility for older integrations."""

    def __str__(self):
        return str(self.get("message", self.get("response", "")))

    def __contains__(self, item):
        if super().__contains__(item):
            return True
        return isinstance(item, str) and item in str(self)


def normalize_error_type(value, default="unknown"):
    text = str(value or "").strip().lower()
    text = ERROR_TYPE_ALIASES.get(text, text)
    return text if text in ERROR_TYPES else default


@dataclass
class ExecutionResult:
    success: bool
    message: str
    action: str = "command"
    target: str | None = None
    verified: bool = False
    status: str = ""
    error_type: str | None = None
    failed_step: int | None = None
    retryable: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("success", "verified", "retryable"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name}_not_bool")

    def to_dict(self):
        status = self.status or ("success" if self.success else "failed")
        error_type = None
        if not self.success and status not in NON_FAILURE_STATUSES:
            error_type = normalize_error_type(self.error_type)
        message = str(self.message or "")
        result = ExecutionResultDict({
            "success": self.success,
            "message": message,
            # Kept during the GUI transition; new code should read message.
            "response": message,
            "action": str(self.action or "command"),
            "target": self.target,
            "verified": self.verified,
            "status": status,
            "error_type": error_type,
            "failed_step": self.failed_step,
            "retryable": self.retryable,
            "data": dict(self.data or {}),
        })
        errors = execution_result_contract_errors(result)
        if errors:
            raise ValueError("invalid_execution_result:" + ",".join(errors))
        return result


def execution_result_contract_errors(value):
    """Return stable contract violations for an execution-boundary result."""
    if not isinstance(value, dict):
        return ("result_not_mapping",)

    errors = []
    missing = EXECUTION_RESULT_FIELDS.difference(value)
    if missing:
        errors.append("missing_fields:" + ",".join(sorted(missing)))
    if "success" in value and type(value["success"]) is not bool:
        errors.append("success_not_bool")
    if "message" in value and not isinstance(value["message"], str):
        errors.append("message_not_string")
    if "action" in value and not isinstance(value["action"], str):
        errors.append("action_not_string")
    if "verified" in value and type(value["verified"]) is not bool:
        errors.append("verified_not_bool")
    if "status" in value and not isinstance(value["status"], str):
        errors.append("status_not_string")
    if "retryable" in value and type(value["retryable"]) is not bool:
        errors.append("retryable_not_bool")
    if "data" in value and not isinstance(value["data"], dict):
        errors.append("data_not_mapping")

    error_type = value.get("error_type")
    if error_type is not None and error_type not in ERROR_TYPES:
        errors.append("unknown_error_type")
    success = value.get("success")
    verified = value.get("verified")
    status = value.get("status")
    if success is True and error_type is not None:
        errors.append("successful_result_has_error_type")
    if success is False and verified is True:
        errors.append("failed_result_is_verified")
    if status in FAILURE_STATUSES and success is True:
        errors.append("failure_status_is_success")
    if (
        success is False
        and status not in NON_FAILURE_STATUSES
        and error_type is None
    ):
        errors.append("failed_result_missing_error_type")
    if status in NON_FAILURE_STATUSES:
        if error_type is not None:
            errors.append("non_terminal_result_has_error_type")
        if verified is True:
            errors.append("non_terminal_result_is_verified")
        if success is True:
            errors.append("non_terminal_result_is_success")
    return tuple(errors)


def is_execution_result(value):
    """Return whether ``value`` satisfies the public execution-result contract."""
    return not execution_result_contract_errors(value)


def success_result(message, action="command", target=None, verified=False, data=None, **extra):
    result = ExecutionResult(
        True, message, action=action, target=target, verified=verified,
        status=extra.pop("status", "success"), data=dict(data or {}),
    ).to_dict()
    result.update(extra)
    errors = execution_result_contract_errors(result)
    if errors:
        raise ValueError("invalid_execution_result:" + ",".join(errors))
    return result


def failure_result(
    message,
    action="command",
    target=None,
    error_type="execution_error",
    failed_step=None,
    retryable=False,
    data=None,
    **extra,
):
    requested_status = str(extra.pop("status", "") or "")
    normalized = (
        None
        if requested_status in NON_FAILURE_STATUSES
        else normalize_error_type(error_type)
    )
    status = requested_status or (
        "cancelled" if normalized == "user_cancelled" else "failed"
    )
    result = ExecutionResult(
        False, message, action=action, target=target, verified=False,
        status=status, error_type=normalized, failed_step=failed_step,
        retryable=retryable, data=dict(data or {}),
    ).to_dict()
    result.update(extra)
    errors = execution_result_contract_errors(result)
    if errors:
        raise ValueError("invalid_execution_result:" + ",".join(errors))
    return result


def confirmation_result(
    message,
    confirmation,
    action="confirmation",
    target=None,
    data=None,
    **extra,
):
    """Return a non-terminal result that waits for an explicit user choice."""
    payload = dict(data or {})
    payload["confirmation"] = dict(confirmation or {})
    result = ExecutionResult(
        False,
        message,
        action=action,
        target=target,
        verified=False,
        status="confirmation_required",
        error_type=None,
        failed_step=None,
        retryable=False,
        data=payload,
    ).to_dict()
    result.update(extra)
    errors = execution_result_contract_errors(result)
    if errors:
        raise ValueError("invalid_execution_result:" + ",".join(errors))
    return result


def clarification_result(
    message,
    clarification,
    action="clarification",
    target=None,
    data=None,
    **extra,
):
    """Return a non-terminal result that waits for missing information."""
    payload = dict(data or {})
    payload["confirmation"] = dict(clarification or {})
    payload["clarification"] = dict(clarification or {})
    result = ExecutionResult(
        False,
        message,
        action=action,
        target=target,
        verified=False,
        status="clarification_required",
        error_type=None,
        failed_step=None,
        retryable=False,
        data=payload,
    ).to_dict()
    result.update(extra)
    errors = execution_result_contract_errors(result)
    if errors:
        raise ValueError("invalid_execution_result:" + ",".join(errors))
    return result


def normalize_execution_result(value, action="command", target=None):
    """Normalize old return values at one compatibility boundary."""
    if isinstance(value, dict) and "success" in value:
        result = ExecutionResultDict(value)
        result.setdefault("message", result.get("response", ""))
        result.setdefault("action", action or "command")
        result.setdefault("target", target)
        # A successful call only means execution completed without an error.
        # Post-condition verification must always be supplied explicitly.
        result.setdefault("verified", False)
        result.setdefault("retryable", False)
        result.setdefault("failed_step", None)
        result.setdefault("data", {})
        if "status" not in result or result["status"] == "":
            result["status"] = (
                "success" if result.get("success") is True else "failed"
            )
        status = result["status"]
        if "error_type" not in result:
            result["error_type"] = (
                None
                if result.get("success") is True or status in NON_FAILURE_STATUSES
                else "unknown"
            )
        elif result.get("error_type") in ERROR_TYPE_ALIASES:
            result["error_type"] = ERROR_TYPE_ALIASES[result["error_type"]]
        errors = execution_result_contract_errors(result)
        if errors:
            return failure_result(
                "실행 결과가 JARVIS 안전 계약을 위반해 실패로 처리했습니다.",
                action=str(action or "command"),
                target=target,
                error_type="contract_error",
                data={"contract_errors": list(errors)},
            )
        result["response"] = result["message"]
        result["data"] = dict(result["data"])
        return result
    # Legacy plugins may still return text. Treat it as a successful message;
    # built-in execution paths return explicit objects and never use text scans.
    return success_result(str(value or ""), action=action, target=target, verified=False)


def result_message(value):
    return normalize_execution_result(value).get("message", "")
