"""Backend-only entry boundary for edit-mode requests."""

from engine.diagnostics.unrecognised_commands import record_refusal
from engine.edit_mode import EditContractError, EditRequest
from engine.execution_result import failure_result, normalize_execution_result


def _remember_refusal(result, request, edit_context):
    """Keep a refused wording so the vocabulary can be fixed.

    Only the sentence, the application and the reason — never anything about
    the document. Failing to write a note must never fail the request.
    """
    try:
        app_type = ""
        if isinstance(edit_context, dict):
            app_type = str(edit_context.get("app_type") or "")
        record_refusal(result, getattr(request, "text", ""), app_type)
    except Exception:
        pass
    return result


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
        return _remember_refusal(
            normalize_execution_result(value, action="edit"), request, edit_context
        )
    except Exception as error:
        return _edit_failure(error, request)


def _edit_failure(error, request):
    """Turn one failed edit into the answer the reader sees.

    Kept out of the route so the route stays a route: try, record, return.
    """
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
    message = str(error)
    if isinstance(recovery, dict):
        if recovery.get("outcome") == "target_changed":
            message += (
                " 문서 확인 전후의 대상이 달라 자동 편집을 멈췄습니다. "
                "현재 문서를 다시 연결해주세요."
            )
        elif recovery.get("outcome") == "unavailable":
            if recovery.get("strategy") == "connected_document_reopen":
                message += (
                    " 연결 문서를 자동으로 다시 열지 못했습니다. 앱의 경고·보호된 "
                    "보기 창을 확인한 뒤 같은 요청을 다시 말해주세요."
                )
            else:
                message += (
                    " 연결 문서를 앞으로 가져오지 못했습니다. 문서 창을 한 번 "
                    "눌러 선택한 뒤 같은 요청을 다시 말해주세요."
                )
    return failure_result(
        message,
        action="edit",
        target=request.edit_session_id,
        error_type=getattr(error, "error_type", "execution_error"),
        failed_step=getattr(error, "failed_step", None),
        retryable=getattr(error, "retryable", False),
        status=getattr(error, "status", "failed"),
        data=data,
    )
