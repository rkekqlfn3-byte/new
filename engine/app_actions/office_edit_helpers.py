"""Shared exact-document acquisition for Word and PowerPoint edit adapters."""

from __future__ import annotations

import os
from contextlib import contextmanager

from engine.app_actions.base import AppActionBlocked, AppActionUnavailable
from engine.app_actions.com_lifecycle import com_apartment
from engine.edit_mode.native_bridge import (
    APP_PROGIDS,
    NativeDocumentBridge,
    _rot_office_reference,
    _same_path,
)


def normalized_path(value) -> str:
    return os.path.normcase(os.path.abspath(str(value or "")))


def office_document_id(document) -> str:
    full_name = str(getattr(document, "FullName", "") or "").strip()
    return normalized_path(full_name) if full_name else ""


def _active_document(application, app_type):
    if app_type == "word":
        return getattr(application, "ActiveDocument", None)
    return getattr(application, "ActivePresentation", None)


@contextmanager
def exact_office_document(
    app_type,
    expected_path,
    *,
    application_getter=None,
    require_visible=True,
    com_runtime=None,
):
    """Yield only the active Office document whose full path exactly matches."""
    expected = normalized_path(expected_path)
    if not expected:
        raise AppActionBlocked("편집할 Office 문서 경로가 비어 있습니다.")
    application = document = active = None
    with com_apartment(com_runtime):
        try:
            if application_getter is not None:
                application = application_getter()
                document = NativeDocumentBridge._office_document(
                    application,
                    app_type,
                    expected,
                )
            else:
                import win32com.client

                try:
                    application = win32com.client.GetActiveObject(
                        APP_PROGIDS[app_type]
                    )
                    document = NativeDocumentBridge._office_document(
                        application,
                        app_type,
                        expected,
                    )
                except Exception:
                    application = document = None
                if document is None:
                    application, document = _rot_office_reference(expected)
            if application is None or document is None:
                raise AppActionUnavailable(
                    "연결된 Office 문서를 네이티브 앱에서 다시 찾지 못했습니다."
                )
            active = _active_document(application, app_type)
            if active is None or not _same_path(
                getattr(active, "FullName", ""),
                expected,
            ):
                raise AppActionBlocked(
                    "연결된 문서가 현재 활성 문서가 아니어서 편집하지 않았습니다."
                )
            if require_visible and not bool(getattr(application, "Visible", False)):
                raise AppActionBlocked(
                    "현재 Office 문서 창이 보이지 않아 편집하지 않았습니다."
                )
            yield application, document
        finally:
            active = None
            document = None
            application = None
