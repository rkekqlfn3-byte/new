"""Canonical command execution results shared by every execution boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ERROR_TYPES = frozenset({
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

    def to_dict(self):
        status = self.status or ("success" if self.success else "failed")
        error_type = None
        if not self.success and status not in NON_FAILURE_STATUSES:
            error_type = normalize_error_type(self.error_type)
        message = str(self.message or "")
        return ExecutionResultDict({
            "success": bool(self.success),
            "message": message,
            # Kept during the GUI transition; new code should read message.
            "response": message,
            "action": str(self.action or "command"),
            "target": self.target,
            "verified": bool(self.verified),
            "status": status,
            "error_type": error_type,
            "failed_step": self.failed_step,
            "retryable": bool(self.retryable),
            "data": dict(self.data or {}),
        })


def success_result(message, action="command", target=None, verified=False, data=None, **extra):
    result = ExecutionResult(
        True, message, action=action, target=target, verified=verified,
        status=extra.pop("status", "success"), data=dict(data or {}),
    ).to_dict()
    result.update(extra)
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
    return result


def normalize_execution_result(value, action="command", target=None):
    """Normalize old return values at one compatibility boundary."""
    if isinstance(value, dict) and "success" in value:
        result = ExecutionResultDict(value)
        message = str(result.get("message", result.get("response", "")) or "")
        result.setdefault("message", message)
        result.setdefault("response", message)
        result.setdefault("action", action)
        result.setdefault("target", target)
        # A successful call only means execution completed without an error.
        # Post-condition verification must always be supplied explicitly.
        result.setdefault("verified", False)
        result.setdefault("status", "success" if result.get("success") else "failed")
        status = result.get("status")
        result.setdefault(
            "error_type",
            None
            if result.get("success") or status in NON_FAILURE_STATUSES
            else "unknown",
        )
        if status in NON_FAILURE_STATUSES:
            result["error_type"] = None
            result["verified"] = False
            result["retryable"] = False
        if result.get("error_type"):
            result["error_type"] = normalize_error_type(result["error_type"])
        result.setdefault("failed_step", None)
        result.setdefault("retryable", False)
        result.setdefault("data", {})
        return result
    # Legacy plugins may still return text. Treat it as a successful message;
    # built-in execution paths return explicit objects and never use text scans.
    return success_result(str(value or ""), action=action, target=target, verified=False)


def result_message(value):
    return normalize_execution_result(value).get("message", "")
