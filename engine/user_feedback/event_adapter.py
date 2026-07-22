"""Adapt existing runtime events and canonical results for display."""

from __future__ import annotations

from typing import Any, Mapping

from engine.execution_result import normalize_execution_result
from engine.user_feedback.event_models import UserFeedbackEvent
from engine.user_feedback.natural_language_renderer import render_user_event


def _data(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _result_target(result: Mapping[str, Any]) -> dict[str, str]:
    data = _data(result.get("data"))
    preview = _data(data.get("preview"))
    target = _data(preview.get("target"))
    if target:
        return target
    structured = _data(data.get("target"))
    return structured


def _undo_available(result: Mapping[str, Any]) -> bool:
    data = _data(result.get("data"))
    return bool(data.get("undo_available") or result.get("undo_available"))


def event_from_execution_result(
    value: Any,
    *,
    execution_id: str = "",
) -> dict[str, Any]:
    """Convert one canonical result without copying its message or content."""
    result = normalize_execution_result(value)
    status = str(result.get("status") or "")
    success = bool(result.get("success"))
    verified = bool(result.get("verified"))
    if status == "confirmation_required":
        event_type = "confirmation_required"
        success_value = None
    elif status == "clarification_required":
        event_type = "clarification_required"
        success_value = None
    elif status == "cancelled" or result.get("error_type") == "user_cancelled":
        event_type = "action_cancelled"
        success_value = False
    elif status == "busy" or result.get("error_type") == "busy":
        event_type = "action_busy"
        success_value = False
    elif success and verified:
        event_type = "verification_passed"
        success_value = True
    elif success:
        event_type = "action_completed"
        success_value = True
    elif result.get("error_type") == "verification_error":
        event_type = "verification_failed"
        success_value = False
    else:
        event_type = "action_failed"
        success_value = False

    event = UserFeedbackEvent(
        event_type=event_type,
        stage="result",
        app=str(_data(result.get("data")).get("app_type") or ""),
        action=str(result.get("action") or "command"),
        execution_id=execution_id,
        target=_result_target(result),
        details={
            "status": status,
            "error_type": result.get("error_type"),
            "failed_step": result.get("failed_step"),
            "retryable": bool(result.get("retryable")),
            "confirmation_pending": status in {
                "confirmation_required", "clarification_required",
            },
        },
        success=success_value,
        verified=verified,
        undo_available=_undo_available(result),
    )
    return render_user_event(event)


def event_from_runtime_event(value: Any) -> dict[str, Any] | None:
    """Convert an existing ExecutionController event using an allowlist only."""
    payload = _data(value)
    action = str(payload.get("action") or "command")
    status = str(payload.get("status") or "running")
    details = _data(payload.get("details"))
    if action == "automatic_recovery" and status in {"retrying", "started"}:
        event_type = "recovery_started"
    elif action == "automatic_recovery" and status in {"success", "resolved"}:
        event_type = "recovery_completed"
    elif action == "automatic_recovery" and status in {"failed", "blocked"}:
        event_type = "recovery_failed"
    elif status == "request_received":
        event_type = "request_received"
    elif status in {"route_selected", "intent_resolved"}:
        event_type = "intent_resolved"
    elif status in {"preparing", "preflight", "pending"}:
        event_type = "action_preparing"
    elif status in {"running", "started"}:
        event_type = "action_started"
    elif status == "confirmation_required":
        event_type = "confirmation_required"
    elif status == "clarification_required":
        event_type = "clarification_required"
    elif status == "success":
        event_type = "action_completed"
    elif status in {"failed", "blocked", "context_changed"}:
        event_type = "action_failed"
    elif status == "cancelled":
        event_type = "action_cancelled"
    else:
        return None

    safe_details = {
        "status": status,
        "error_type": details.get("error_type"),
        "failed_step": details.get("failed_step"),
        "retryable": details.get("retryable"),
        "reason": details.get("reason"),
        "attempt": details.get("attempt"),
        "max_attempts": details.get("max_attempts"),
    }
    event = UserFeedbackEvent(
        event_type=event_type,
        stage="runtime",
        app=str(payload.get("app") or ""),
        action=action,
        execution_id=str(payload.get("execution_id") or ""),
        target=payload.get("target"),
        details=safe_details,
        success=(True if status == "success" else None),
        verified=False,
        undo_available=False,
        created_at=str(payload.get("time") or ""),
    )
    return render_user_event(event)
