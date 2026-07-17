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
        return callback(
            context.payload,
            **{"chat_session_id": context.session_id},
            confirmation_id=context.consumed.get("confirmation_id"),
            log_callback=context.log_callback,
        )
    except Exception as error:
        return failure_result(
            str(error),
            action="edit",
            target=context.payload.get("edit_request", {}).get("edit_session_id"),
            error_type=getattr(error, "error_type", "execution_error"),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
        )
