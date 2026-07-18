"""Eel endpoints for local edit-document connection and session state."""

from __future__ import annotations

import logging

import eel

from engine.core import get_parser
from engine.app_actions import vba_trust_status
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
        retryable=bool(getattr(error, "retryable", False)),
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
            context=session.get("context"),
            context_error=session.get("context_error"),
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
            context=session.get("context"),
            context_error=session.get("context_error"),
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
            context=session.get("context"),
            context_error=session.get("context_error"),
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
            context=session.get("context"),
            context_error=session.get("context_error"),
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
def get_edit_context(session_id=None):
    try:
        controller = _get_controller()
        context = controller.context(session_id)
        session = controller.session_manager.current()
        return success_result(
            "현재 문서 선택 영역을 확인했습니다.",
            action="edit_context",
            target=context.get("session_id"),
            verified=True,
            data={
                "context": context,
                "session": session,
                "direct_edit_feedback": controller.direct_edit_feedback(),
            },
        )
    except Exception as error:
        if getattr(error, "status", None) == "stale_context":
            logger.debug("Edit context is temporarily unavailable: %s", error)
        else:
            logger.exception("Edit context lookup failed")
        return _failure(error, action="edit_context")


@eel.expose
def get_user_preference_learning_status():
    try:
        status = _get_controller().user_preference_learning_status()
        return success_result(
            "명시적 사용자 선호 학습 상태를 확인했습니다.",
            action="user_preference_learning",
            verified=True,
            data=status,
        )
    except Exception as error:
        logger.exception("User preference learning status lookup failed")
        return _failure(error, action="user_preference_learning")


@eel.expose
def get_excel_vba_trust_status():
    """Report configuration only; never change Excel security settings."""
    try:
        status = vba_trust_status()
        return success_result(
            "Excel VBA 프로젝트 접근 보안 상태를 확인했습니다.",
            action="excel_vba_trust_status",
            verified=True,
            data=status,
        )
    except Exception as error:
        logger.exception("Excel VBA trust status lookup failed")
        return _failure(error, action="excel_vba_trust_status")


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


@eel.expose
def set_edit_selection_overlay(enabled):
    try:
        result = _get_controller().set_selection_overlay(enabled)
        return success_result(
            "Excel 선택 영역 표시 설정을 변경했습니다.",
            action="edit_selection_overlay",
            verified=True,
            data=result,
        )
    except Exception as error:
        logger.exception("Edit selection-overlay setting failed")
        return _failure(error, action="edit_selection_overlay")
