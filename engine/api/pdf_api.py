"""Eel endpoints for one explicit, read-only PDF connection."""

from __future__ import annotations

import logging

import eel

from engine.core import get_parser
from engine.execution_result import failure_result, success_result
from engine.pdf.errors import PdfReadError, PdfReadErrorCode

logger = logging.getLogger(__name__)
parser = None


def _get_manager():
    active_parser = parser if parser is not None else get_parser()
    return active_parser.pdf_intake_manager


def _failure(error, action="pdf_connect"):
    code = str(getattr(error, "code", "pdf_connection_error"))
    if isinstance(getattr(error, "code", None), PdfReadErrorCode):
        code = error.code.value
    status = str(getattr(error, "status", "blocked"))
    outcome = str(getattr(error, "outcome", "validation_error"))
    if outcome == "cancelled":
        status = "cancelled"
    error_type = str(getattr(error, "error_type", "validation_error"))
    if outcome == "cancelled":
        error_type = "user_cancelled"
    if outcome == "environment_blocked":
        error_type = "environment_error"
    return failure_result(
        str(error),
        action=action,
        error_type=error_type,
        status=status,
        data={"pdf_error": {"code": code, "outcome": outcome}},
    )


@eel.expose
def choose_and_connect_pdf_document():
    try:
        connection = _get_manager().choose_and_connect()
        if connection is None:
            return failure_result(
                "PDF 선택을 취소했습니다.",
                action="pdf_connect",
                error_type="user_cancelled",
                status="cancelled",
            )
        return success_result(
            f"{connection['document_name']} PDF를 읽기 전용으로 연결했습니다.",
            action="pdf_connect",
            target=connection["connection_id"],
            verified=True,
            data={"connection": connection},
        )
    except (PdfReadError, RuntimeError, ValueError, OSError) as error:
        logger.info("PDF chooser connection rejected type=%s", type(error).__name__)
        return _failure(error)


@eel.expose
def connect_pdf_document(file_path):
    try:
        connection = _get_manager().connect_file(file_path)
        return success_result(
            f"{connection['document_name']} PDF를 읽기 전용으로 연결했습니다.",
            action="pdf_connect",
            target=connection["connection_id"],
            verified=True,
            data={"connection": connection},
        )
    except (PdfReadError, RuntimeError, ValueError, OSError) as error:
        logger.info("PDF path connection rejected type=%s", type(error).__name__)
        return _failure(error)


@eel.expose
def connect_dropped_pdf_document(file_name, file_size=None, path_hint=None):
    try:
        connection = _get_manager().connect_dropped(file_name, file_size, path_hint)
        return success_result(
            f"{connection['document_name']} PDF를 읽기 전용으로 연결했습니다.",
            action="pdf_connect",
            target=connection["connection_id"],
            verified=True,
            data={"connection": connection},
        )
    except (PdfReadError, RuntimeError, ValueError, OSError) as error:
        logger.info("Dropped PDF connection rejected type=%s", type(error).__name__)
        return _failure(error)


@eel.expose
def get_pdf_connection_status():
    try:
        status = _get_manager().status(revalidate=True)
        return success_result(
            "PDF 연결 상태를 확인했습니다.",
            action="pdf_connection_status",
            target=(status["connection"] or {}).get("connection_id"),
            verified=True,
            data=status,
        )
    except (PdfReadError, RuntimeError, ValueError) as error:
        logger.info("PDF connection status rejected type=%s", type(error).__name__)
        return _failure(error, action="pdf_connection_status")


@eel.expose
def disconnect_pdf_document(connection_id=None):
    try:
        connection = _get_manager().disconnect(connection_id)
        return success_result(
            "PDF 연결을 해제했습니다. 원본 파일은 변경하지 않았습니다.",
            action="pdf_disconnect",
            target=connection["connection_id"],
            verified=True,
            data={"connection": connection},
        )
    except (RuntimeError, ValueError) as error:
        logger.info("PDF disconnect rejected type=%s", type(error).__name__)
        return _failure(error, action="pdf_disconnect")


@eel.expose
def resolve_pdf_target_reference(command):
    try:
        reference = _get_manager().resolve_reference(command)
        return success_result(
            "현재 PDF 요청 대상을 확인했습니다.",
            action="pdf_reference",
            target=reference.connection_id,
            verified=True,
            data={"reference": reference.to_dict()},
        )
    except (PdfReadError, RuntimeError, ValueError) as error:
        logger.info("PDF reference resolution rejected type=%s", type(error).__name__)
        return _failure(error, action="pdf_reference")


@eel.expose
def resolve_pdf_command(command):
    try:
        request = _get_manager().resolve_command(command)
        return success_result(
            "현재 PDF 요청 의도와 대상을 확인했습니다.",
            action="pdf_command_resolve",
            target=request.reference.connection_id,
            verified=True,
            data={"request": request.to_evidence_dict()},
        )
    except (PdfReadError, RuntimeError, ValueError) as error:
        logger.info("PDF command resolution rejected type=%s", type(error).__name__)
        return _failure(error, action="pdf_command_resolve")
