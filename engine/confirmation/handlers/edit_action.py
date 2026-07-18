"""Resume one explicitly approved Stage 5 document edit."""

from engine.execution_result import failure_result


def resolve(owner, context):
    controller = getattr(owner, "edit_mode_controller", None)
    if context.option_id == "rewrite":
        callback = getattr(controller, "rewrite_pending_edit", None)
    else:
        callback = getattr(controller, "resolve_prepared_edit", None)
    if not callable(callback):
        return failure_result(
            "편집 작업을 이어서 실행할 수 없습니다.",
            action="edit",
            error_type="environment_error",
            status="blocked",
        )
    try:
        kwargs = {
            "chat_session_id": context.session_id,
            "confirmation_id": context.consumed.get("confirmation_id"),
            "log_callback": context.log_callback,
        }
        if context.option_id == "rewrite":
            kwargs["feedback_text"] = context.feedback_text
        return callback(context.payload, **kwargs)
    except Exception as error:
        diagnostic_context = getattr(error, "diagnostic_context", None)
        diagnostic_context = (
            dict(diagnostic_context)
            if isinstance(diagnostic_context, dict)
            else {}
        )
        recovery = diagnostic_context.get("automatic_recovery")
        data = {"diagnostic_context": diagnostic_context}
        if isinstance(recovery, dict):
            data["automatic_recovery"] = dict(recovery)
            data["retry_count"] = int(recovery.get("retry_count") or 0)
            data["triage_hints"] = {
                "intent_understood": True,
                "target_resolved": bool(recovery.get("target_resolved")),
                "recovery_target_changed": (
                    recovery.get("outcome") == "target_changed"
                ),
                "environment_blocked": (
                    recovery.get("outcome") == "unavailable"
                ),
            }
        return failure_result(
            str(error),
            action="edit",
            target=context.payload.get("edit_request", {}).get("edit_session_id"),
            error_type=getattr(error, "error_type", "execution_error"),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
            data=data,
        )
