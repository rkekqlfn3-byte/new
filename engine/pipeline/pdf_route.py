"""Fail-closed command route for the explicitly connected PDF."""

from __future__ import annotations

from engine.execution_result import failure_result, success_result
from engine.pdf import (
    PdfIntentKind,
    PdfReadError,
    PdfReadErrorCode,
    PdfReferenceError,
    looks_like_pdf_command,
)

_INTENT_LABELS = {
    PdfIntentKind.SUMMARY: "요약",
    PdfIntentKind.EXPLAIN: "설명",
    PdfIntentKind.SEARCH: "검색",
    PdfIntentKind.PAGE_COUNT: "페이지 수 확인",
    PdfIntentKind.TABLE_OF_CONTENTS: "목차 확인",
    PdfIntentKind.TABLE_EXTRACT: "표 추출",
    PdfIntentKind.REPORT: "보고서 작성",
    PdfIntentKind.SPLIT: "분할",
    PdfIntentKind.MERGE: "병합",
    PdfIntentKind.ROTATE: "회전",
}


def _failure(error, *, action="pdf_command"):
    status = str(getattr(error, "status", "failed"))
    error_type = str(getattr(error, "error_type", "validation_error"))
    code = getattr(error, "code", "pdf_command_error")
    if isinstance(code, PdfReadErrorCode):
        code = code.value
    if isinstance(error, PdfReadError):
        if error.code is PdfReadErrorCode.DEPENDENCY_UNAVAILABLE:
            error_type = "environment_error"
        elif error.code is PdfReadErrorCode.TIMEOUT:
            error_type = "timeout"
        elif error.code is PdfReadErrorCode.CANCELLED:
            status = "cancelled"
            error_type = "user_cancelled"
    return failure_result(
        str(error),
        action=action,
        error_type=error_type,
        status=status,
        data={"pdf_error": {"code": str(code)}},
    )


def _unimplemented(request):
    intent = request.intent
    label = _INTENT_LABELS[intent.kind]
    evidence = request.to_evidence_dict()
    evidence["triage_hints"] = {
        "intent_understood": True,
        "target_resolved": True,
        "capability_exists": False,
    }
    if intent.requires_confirmation:
        message = (
            f"PDF {label} 요청과 대상을 확인했지만 파일 변경 기능은 "
            "PDF-4에서 승인·원자적 출력·검증을 붙인 뒤 실행합니다. "
            "지금은 원본이나 새 파일을 변경하지 않았습니다."
        )
    else:
        message = (
            f"PDF {label} 요청과 페이지 대상을 확인했지만 내용 답변·산출물 "
            "기능은 PDF-3에서 근거 페이지와 검증을 붙인 뒤 실행합니다."
        )
    return failure_result(
        message,
        action=f"pdf_{intent.kind.value}",
        error_type="validation_error",
        status="blocked",
        data=evidence,
    )


def try_execute_pdf_route(parser, raw_input, *, log_callback=None):
    manager = getattr(parser, "pdf_intake_manager", None)
    if manager is None:
        return None
    connected = bool(manager.status().get("connected"))
    if not looks_like_pdf_command(raw_input, connected=connected):
        return None
    try:
        request = manager.resolve_command(raw_input)
        if log_callback:
            log_callback(
                "[PDF] intent="
                f"{request.intent.kind.value} pages={len(request.reference.page_numbers)}"
            )
        if request.intent.kind is not PdfIntentKind.PAGE_COUNT:
            return _unimplemented(request)
        connection = manager.current()
        if connection.connection_id != request.reference.connection_id:
            raise PdfReferenceError(
                "pdf_connection_replaced",
                "PDF 페이지 수를 확인하는 동안 연결 대상이 바뀌었습니다.",
            )
        return success_result(
            f"현재 연결된 PDF는 총 {connection.document.page_count}페이지입니다.",
            action="pdf_page_count",
            target=connection.connection_id,
            verified=True,
            data={"pdf_request": request.to_evidence_dict()},
        )
    except (PdfReadError, PdfReferenceError, RuntimeError, ValueError) as error:
        return _failure(error)
