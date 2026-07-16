"""Eel endpoints for local edit-document connection and session state."""

from __future__ import annotations

import logging

import eel

from engine.core import get_parser
from engine.execution_result import failure_result, success_result


logger = logging.getLogger(__name__)
parser = None


def _get_controller():
    active_parser = parser if parser is not None else get_parser()
    return active_parser.edit_mode_controller


def _success(message, session=None, **data):
    payload = dict(data)
    payload["session"] = session
    return success_result(
        message,
        action="edit_connect",
        target=session.get("session_id") if session else None,
        verified=bool(session),
        data=payload,
    )


def _failure(error, action="edit_connect"):
    candidates = getattr(error, "candidates", ())
    return failure_result(
        str(error),
        action=action,
        error_type=getattr(error, "error_type", "execution_error"),
        status=getattr(error, "status", "failed"),
        data={"candidates": [dict(item) for item in candidates]},
    )


@eel.expose
def choose_and_connect_edit_document():
    try:
        session = _get_controller().choose_and_connect()
        if session is None:
            return failure_result(
                "문서 선택을 취소했습니다.",
                action="edit_connect",
                error_type="user_cancelled",
                status="cancelled",
            )
        return _success(
            f"{session['document_name']} 문서를 편집 대상으로 연결했습니다.",
            session,
            layout=session.get("layout"),
        )
    except Exception as error:
        logger.exception("Edit document chooser connection failed")
        return _failure(error)


@eel.expose
def connect_edit_document(file_path):
    try:
        session = _get_controller().connect_file(file_path)
        return _success(
            f"{session['document_name']} 문서를 편집 대상으로 연결했습니다.",
            session,
            layout=session.get("layout"),
        )
    except Exception as error:
        logger.exception("Edit document path connection failed")
        return _failure(error)


@eel.expose
def connect_dropped_edit_document(file_name, file_size=None, path_hint=None):
    try:
        session = _get_controller().connect_dropped_document(
            file_name=file_name,
            file_size=file_size,
            path_hint=path_hint,
        )
        return _success(
            f"{session['document_name']} 문서를 편집 대상으로 연결했습니다.",
            session,
            layout=session.get("layout"),
        )
    except Exception as error:
        logger.exception("Dropped edit document connection failed")
        return _failure(error)


@eel.expose
def connect_active_edit_document(app_type=None):
    try:
        session = _get_controller().connect_active_document(app_type)
        return _success(
            f"열려 있던 {session['document_name']} 문서를 편집 대상으로 연결했습니다.",
            session,
            layout=session.get("layout"),
        )
    except Exception as error:
        logger.exception("Active edit document connection failed")
        return _failure(error)


@eel.expose
def get_edit_session_status():
    try:
        status = _get_controller().status()
        return success_result(
            "편집 세션 상태를 확인했습니다.",
            action="edit_session",
            verified=True,
            data=status,
        )
    except Exception as error:
        logger.exception("Edit session status lookup failed")
        return _failure(error, action="edit_session")


@eel.expose
def disconnect_edit_document(session_id=None):
    try:
        disconnected = _get_controller().disconnect(session_id)
        return success_result(
            "편집 문서 연결을 해제했습니다. 문서 앱은 종료하지 않았습니다.",
            action="edit_disconnect",
            target=disconnected.get("session_id"),
            verified=True,
            data={"session": disconnected},
        )
    except Exception as error:
        logger.exception("Edit document disconnect failed")
        return _failure(error, action="edit_disconnect")


@eel.expose
def set_edit_auto_layout(enabled):
    try:
        result = _get_controller().set_auto_layout(enabled)
        return success_result(
            "문서 창 자동 배치 설정을 변경했습니다.",
            action="edit_layout",
            verified=True,
            data=result,
        )
    except Exception as error:
        logger.exception("Edit auto-layout setting failed")
        return _failure(error, action="edit_layout")
