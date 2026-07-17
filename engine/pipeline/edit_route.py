"""Backend-only entry boundary for edit-mode requests."""

from engine.edit_mode import EditContractError, EditRequest
from engine.execution_result import failure_result, normalize_execution_result


def execute_edit_route(
    parser,
    user_input,
    *,
    edit_context=None,
    session_id=None,
    log_callback=None,
):
    try:
        request = EditRequest.from_input(user_input, edit_context)
    except EditContractError as error:
        return failure_result(
            str(error),
            action="edit",
            error_type="validation_error",
            status="blocked",
        )

    handler = getattr(parser, "edit_mode_handler", None)
    if handler is None:
        return failure_result(
            "편집할 문서를 먼저 연결해주세요. 문서 연결 전에는 편집 명령을 실행하지 않습니다.",
            action="edit",
            target=request.edit_session_id,
            error_type="validation_error",
            status="blocked",
            data={
                "edit_session_id": request.edit_session_id,
                "request_id": request.request_id,
                "chat_session_id": str(session_id or ""),
            },
        )
    if log_callback:
        log_callback(
            f"[Edit] session={request.edit_session_id} request={request.request_id}"
        )
    try:
        if callable(handler):
            value = handler(request)
        else:
            callback = getattr(handler, "handle", None)
            if not callable(callback):
                raise TypeError("편집 핸들러는 callable 또는 handle()을 제공해야 합니다.")
            value = callback(
                request,
                chat_session_id=session_id,
                log_callback=log_callback,
            )
        if value is None:
            raise TypeError("편집 핸들러는 callable 또는 handle()을 제공해야 합니다.")
        return normalize_execution_result(value, action="edit")
    except Exception as error:
        diagnostic_context = getattr(error, "diagnostic_context", None)
        return failure_result(
            str(error),
            action="edit",
            target=request.edit_session_id,
            error_type=getattr(error, "error_type", "execution_error"),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
            data={
                "diagnostic_context": (
                    dict(diagnostic_context)
                    if isinstance(diagnostic_context, dict)
                    else {}
                )
            },
        )
