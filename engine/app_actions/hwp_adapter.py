"""Safe native actions for the active Hanword document through HwpObject."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionUnavailable,
)
from engine.app_actions.com_lifecycle import (
    OfficeApplicationLease,
    application_lease,
    com_apartment,
)
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.app_actions.office_undo_services import HwpUndoService
from engine.app_actions.operations.hwp import (
    COLOR_RGB,
    HWP_OPERATIONS,
    PARAGRAPH_ALIGNMENTS,
    char_state,
    paragraph_state,
)
from engine.app_actions.operations.hwp.base import MAX_DOCUMENT_TEXT_CHARS

# Re-exported for callers that imported these from the adapter before the
# per-operation split.
__all__ = [
    "COLOR_RGB",
    "MAX_DOCUMENT_TEXT_CHARS",
    "PARAGRAPH_ALIGNMENTS",
    "HwpAdapter",
    "create_owned_hwp_application",
]


def _default_hwp_getter():
    import pythoncom
    import win32com.client

    context = pythoncom.CreateBindCtx(0)
    running_table = pythoncom.GetRunningObjectTable()
    candidates = []
    for moniker in running_table.EnumRunning():
        try:
            name = moniker.GetDisplayName(context, moniker)
        except Exception:
            continue
        if not str(name).startswith("!HwpObject."):
            continue
        try:
            raw = running_table.GetObject(moniker)
            hwp = win32com.client.gencache.EnsureDispatch(
                raw.QueryInterface(pythoncom.IID_IDispatch)
            )
            if int(hwp.XHwpDocuments.Count) < 1:
                continue
            window = hwp.XHwpWindows.Active_XHwpWindow
            if bool(window.Visible):
                candidates.append(hwp)
        except Exception:
            continue
    if not candidates:
        raise AppActionUnavailable(
            "실행 중인 한글 Automation 문서를 찾지 못했습니다. 한글에서 문서를 먼저 열어주세요."
        )
    if len(candidates) > 1:
        raise AppActionBlocked(
            "서로 다른 한글 Automation 인스턴스가 여러 개 실행 중이라 대상을 안전하게 정하지 못했습니다. 하나만 남겨주세요."
        )
    return candidates[0]


def create_owned_hwp_application(application_factory=None):
    """Create a dedicated HWP automation instance owned by Jarvis."""
    if application_factory is None:
        import win32com.client

        application_factory = win32com.client.DispatchEx
    application = application_factory("HWPFrame.HwpObject")
    return OfficeApplicationLease(
        application=application,
        owns_application=True,
        application_kind="hwp",
    )


class HwpAdapter:
    """COM lifecycle, document identity and the shared result shape.

    Every user-visible action lives in ``engine.app_actions.operations.hwp``.
    """

    supported_operations = HWP_OPERATIONS.names

    # Shared live-state readers. Operations and ``HwpUndoService`` both use
    # these, so they stay reachable from the adapter.
    _char_state = staticmethod(char_state)
    _paragraph_state = staticmethod(paragraph_state)

    def __init__(
        self,
        object_getter=None,
        require_visible=True,
        enable_pdf_export=False,
        com_runtime=None,
        owned_unsaved_document_path=None,
    ):
        if owned_unsaved_document_path and object_getter is None:
            raise ValueError(
                "미저장 한글 문서 ID는 전용 소유 HwpObject getter와 함께만 사용할 수 있습니다."
            )
        self._object_getter = object_getter or _default_hwp_getter
        self._com_runtime = com_runtime
        self._require_visible = bool(require_visible)
        self._enable_pdf_export = bool(enable_pdf_export)
        self._owned_unsaved_document_path = (
            os.path.normcase(os.path.abspath(str(owned_unsaved_document_path)))
            if owned_unsaved_document_path
            else None
        )

    @contextmanager
    def _hwp(self):
        with com_apartment(self._com_runtime):
            with self._hwp_reference() as hwp:
                yield hwp

    @contextmanager
    def _hwp_reference(self):
        lease = None
        try:
            lease = application_lease(self._object_getter(), "hwp")
            hwp = lease.application
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionUnavailable(
                "실행 중인 한글 문서에 연결하지 못했습니다."
            ) from error
        if hwp is None:
            raise AppActionUnavailable("실행 중인 한글 문서를 찾지 못했습니다.")
        try:
            yield hwp
        finally:
            lease.cleanup()
            hwp = None

    _created_at = staticmethod(prepared_at_timestamp)

    @staticmethod
    def _digest_text(value):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()

    _state_fingerprint = staticmethod(stable_state_fingerprint)

    @staticmethod
    def _selection_info(hwp):
        raw = tuple(hwp.GetSelectedPos())
        has_selection = bool(raw[0]) if raw else False
        coordinates = [int(value) for value in raw[1:7]] if len(raw) >= 7 else []
        selected_text = (
            str(hwp.GetTextFile("UNICODE", "saveblock")) if has_selection else ""
        )
        return {
            "has_selection": has_selection,
            "coordinates": coordinates,
            "text": selected_text,
            "text_length": len(selected_text),
            "text_digest": HwpAdapter._digest_text(selected_text),
        }

    def _context(self, hwp):
        try:
            documents = hwp.XHwpDocuments
            if int(documents.Count) < 1:
                raise AppActionUnavailable("한글에 열린 문서가 없습니다.")
            document = documents.Active_XHwpDocument
            window = hwp.XHwpWindows.Active_XHwpWindow
            if self._require_visible and not bool(window.Visible):
                raise AppActionBlocked("현재 한글 문서 창이 보이지 않아 작업하지 않습니다.")
            edit_mode = int(document.EditMode)
            if edit_mode == 0:
                raise AppActionBlocked("현재 한글 문서는 읽기 전용이라 변경하지 않습니다.")
            if edit_mode != 1:
                raise AppActionBlocked("현재 한글 문서가 일반 편집 모드가 아닙니다.")
            full_name = str(document.FullName or "").strip()
            window_handle = int(window.WindowHandle or 0)
            if full_name:
                document_id = os.path.normcase(os.path.abspath(full_name))
                document_name = os.path.basename(full_name)
            elif self._owned_unsaved_document_path:
                document_id = self._owned_unsaved_document_path
                document_name = os.path.basename(document_id)
            else:
                document_id = f"unsaved:{window_handle}"
                document_name = "저장되지 않은 문서"
            document_text = str(hwp.GetTextFile("UNICODE", ""))
            if len(document_text) > MAX_DOCUMENT_TEXT_CHARS:
                raise AppActionBlocked(
                    f"현재 문서 텍스트가 {MAX_DOCUMENT_TEXT_CHARS:,}자를 넘어 1차 한글 자동화 범위를 벗어납니다."
                )
            position = [int(value) for value in hwp.GetPos()]
            selection = self._selection_info(hwp)
            base = {
                "document_id": document_id,
                "document_name": document_name,
                "full_name": full_name,
                "window_handle": window_handle,
                "document_count": int(documents.Count),
                "edit_mode": edit_mode,
                "is_modified": bool(hwp.IsModified),
                "position": position,
                "selection": {
                    key: value for key, value in selection.items() if key != "text"
                },
                "text_length": len(document_text),
                "text_digest": self._digest_text(document_text),
            }
            return document, base, document_text, selection
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionUnavailable(
                "한글의 활성 문서·선택 영역·커서 상태를 읽지 못했습니다."
            ) from error

    def read_selection(self):
        """Return the full active selection for local edit preparation only."""
        with self._hwp() as hwp:
            _, base, _, selection = self._context(hwp)
            return {
                "document_id": base["document_id"],
                "has_selection": selection["has_selection"],
                "text": selection["text"],
                "text_length": selection["text_length"],
                "text_digest": selection["text_digest"],
                "position": list(base["position"]),
                "coordinates": list(selection["coordinates"]),
            }

    @staticmethod
    def _target(base, selection, paragraph=False):
        if selection["has_selection"]:
            return "선택 영역"
        if paragraph:
            return "현재 문단"
        return "커서 위치 " + ":".join(str(value) for value in base["position"])

    @staticmethod
    def _ensure_same_context(current, prepared):
        if current.context_fingerprint != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인하는 동안 한글의 활성 문서·커서·선택 영역 또는 문서 내용이 바뀌어 실행하지 않았습니다."
            )

    def prepare(self, operation, params):
        operation_module = HWP_OPERATIONS.require(operation)
        if not isinstance(params, dict):
            raise AppActionBlocked("한글 작업의 params는 객체 형식이어야 합니다.")
        with self._hwp() as hwp:
            return operation_module.prepare(self, hwp, params)

    def execute(self, prepared):
        operation_module = HWP_OPERATIONS.require_prepared(prepared)
        with self._hwp() as hwp:
            return operation_module.execute(self, hwp, prepared)

    @staticmethod
    def _undo(hwp):
        hwp.HAction.Run("Undo")

    def undo(self, prepared, record=None):
        """Undo one verified JARVIS edit and prove the structured snapshot returned."""
        return HwpUndoService(self, PARAGRAPH_ALIGNMENTS).execute(
            prepared, record
        )

    @staticmethod
    def _result(prepared, before, after, changed):
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "hwp",
            "operation": prepared.operation,
            "document_id": prepared.document_id,
            "workbook_name": prepared.workbook_name,
            "sheet": prepared.sheet,
            "target": prepared.target,
            "changed": bool(changed),
            "verification_method": prepared.verification_method,
            "before": before,
            "after": after,
        }
