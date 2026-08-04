"""Safe native actions for one selected PowerPoint shape or text range."""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    PreparedAction,
)
from engine.app_actions.office_edit_helpers import (
    exact_office_document,
    office_document_id,
)
from engine.app_actions.office_undo_services import PowerPointUndoService
from engine.app_actions.operations.powerpoint import (
    POWERPOINT_OPERATIONS,
    PPT_ALIGNMENTS,
    PowerPointSession,
)
from engine.app_actions.operations.powerpoint.base import (
    MAX_PPT_TEXT_CHARS,
    MSO_PLACEHOLDER,
    PPT_SELECTION_SHAPES,
    PPT_SELECTION_TEXT,
)

PPT_TRANSIENT_COM_ERRORS = frozenset(
    {
        -2147418111,  # RPC_E_CALL_REJECTED
        -2147417846,  # RPC_E_SERVERCALL_RETRYLATER
        -2147188160,  # PowerPoint temporarily reports no active presentation
    }
)
PPT_PROXY_REACQUIRE_ERRORS = frozenset(
    {
        -2147417848,  # RPC_E_DISCONNECTED / stale PowerPoint dispatch proxy
        -2147023174,  # RPC_S_SERVER_UNAVAILABLE while a proxy is being renewed
    }
)
PPT_COM_RETRY_ATTEMPTS = 8
PPT_COM_RETRY_DELAY_SECONDS = 0.05

# Re-exported for callers that imported these from the adapter before the
# per-operation split.
__all__ = ["PPT_ALIGNMENTS", "PowerPointAdapter"]


class PowerPointAdapter:
    """COM lifecycle, slide/shape identity, retry policy and the result shape.

    Every user-visible action lives in
    ``engine.app_actions.operations.powerpoint``.  The geometry and style
    writers below stay here because ``PowerPointUndoService`` uses them to
    restore a verified edit.
    """

    supported_operations = POWERPOINT_OPERATIONS.names

    def __init__(
        self,
        application_getter=None,
        *,
        require_visible=True,
        com_runtime=None,
    ):
        self._application_getter = application_getter
        self._require_visible = bool(require_visible)
        self._com_runtime = com_runtime

    @staticmethod
    def _digest(value):
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _item(collection, index):
        item = getattr(collection, "Item", None)
        if item is not None:
            return item(index)
        return collection(index)

    @staticmethod
    def _com_error_code(error):
        """Return a nested COM HRESULT without depending on pywin32 error types."""
        pending = [error]
        visited = set()
        first_code = None
        while pending:
            current = pending.pop()
            marker = id(current)
            if marker in visited:
                continue
            visited.add(marker)
            hresult = getattr(current, "hresult", None)
            if isinstance(hresult, int):
                if hresult in PPT_TRANSIENT_COM_ERRORS:
                    return hresult
                first_code = first_code if first_code is not None else hresult
            values = (
                getattr(current, "args", ())
                if isinstance(current, BaseException)
                else current
            )
            if isinstance(values, dict):
                values = tuple(values.values())
            elif not isinstance(values, (tuple, list)):
                values = ()
            for value in values:
                if isinstance(value, int):
                    if value in PPT_TRANSIENT_COM_ERRORS:
                        return value
                    first_code = first_code if first_code is not None else value
                elif isinstance(value, (BaseException, tuple, list, dict)):
                    pending.append(value)
            cause = getattr(current, "__cause__", None)
            context = getattr(current, "__context__", None)
            if cause is not None:
                pending.append(cause)
            if context is not None:
                pending.append(context)
        return first_code

    def _retry_transient_com(
        self,
        callback,
        *,
        include_proxy_reacquire=False,
        write_started=None,
    ):
        """Retry bounded read/acquisition failures without replaying a write."""
        for attempt in range(PPT_COM_RETRY_ATTEMPTS):
            try:
                return callback()
            except Exception as error:
                code = self._com_error_code(error)
                proxy_retry_allowed = (
                    bool(include_proxy_reacquire)
                    and code in PPT_PROXY_REACQUIRE_ERRORS
                    and not (callable(write_started) and write_started())
                )
                if (
                    code not in PPT_TRANSIENT_COM_ERRORS
                    and not proxy_retry_allowed
                    or attempt + 1 >= PPT_COM_RETRY_ATTEMPTS
                ):
                    raise
                try:
                    import pythoncom

                    pythoncom.PumpWaitingMessages()
                except Exception:
                    pass
                time.sleep(PPT_COM_RETRY_DELAY_SECONDS * (attempt + 1))

    @staticmethod
    def _document_path(params):
        path = str(params.get("document_path") or "").strip()
        if not path or not os.path.isfile(path):
            raise AppActionBlocked("연결된 PowerPoint 문서 경로를 확인하지 못했습니다.")
        return path

    @staticmethod
    def _shape_by_id(slide, shape_id):
        shapes = slide.Shapes
        for index in range(1, int(shapes.Count) + 1):
            shape = shapes.Item(index)
            if int(shape.Id) == int(shape_id):
                return shape
            shape = None
        raise AppActionBlocked("선택한 PowerPoint Shape를 다시 찾지 못했습니다.")

    @staticmethod
    def _slide_by_id(presentation, slide_id, slide_index=None):
        slides = presentation.Slides
        if slide_index is not None:
            candidate = slides.Item(int(slide_index))
            if int(candidate.SlideID) == int(slide_id):
                return candidate
            candidate = None
        for index in range(1, int(slides.Count) + 1):
            slide = slides.Item(index)
            if int(slide.SlideID) == int(slide_id):
                return slide
            slide = None
        raise AppActionBlocked("선택한 PowerPoint 슬라이드를 다시 찾지 못했습니다.")

    @classmethod
    def _shape_value(cls, slide, shape_id, attribute):
        shape = cls._shape_by_id(slide, shape_id)
        try:
            return getattr(shape, attribute)
        finally:
            shape = None

    @classmethod
    def _geometry_state(cls, slide, shape_id):
        # A fresh Shapes.Item proxy per scalar prevents a long-lived dispatch
        # object from failing late at e.g. Item.LockAspectRatio.
        return {
            "left": float(cls._shape_value(slide, shape_id, "Left")),
            "top": float(cls._shape_value(slide, shape_id, "Top")),
            "width": float(cls._shape_value(slide, shape_id, "Width")),
            "height": float(cls._shape_value(slide, shape_id, "Height")),
            "rotation": float(cls._shape_value(slide, shape_id, "Rotation")),
            "lock_aspect_ratio": int(
                cls._shape_value(slide, shape_id, "LockAspectRatio")
            ),
        }

    @staticmethod
    def _has_text_frame(shape):
        try:
            return int(shape.HasTextFrame) != 0
        except Exception:
            return False

    @staticmethod
    def _style_state(text_range):
        try:
            font = text_range.Font
            paragraph = text_range.ParagraphFormat
            color = None
            try:
                color = int(font.Color.RGB)
            except Exception:
                pass
            return {
                "bold": int(font.Bold),
                "font_size": float(font.Size),
                "font_name": str(font.Name or ""),
                "font_color": color,
                "alignment": int(paragraph.Alignment),
            }
        except Exception as error:
            raise AppActionBlocked(
                "PowerPoint 선택 텍스트의 서식을 읽지 못했습니다."
            ) from error

    def _context(self, application, presentation):
        if bool(getattr(presentation, "ReadOnly", False)):
            raise AppActionBlocked(
                "현재 PowerPoint 프레젠테이션은 읽기 전용이라 변경하지 않습니다."
            )
        window = application.ActiveWindow
        if window is None:
            raise AppActionBlocked("PowerPoint 활성 편집 창을 찾지 못했습니다.")
        selected_slide = window.View.Slide
        slide_index = int(selected_slide.SlideIndex)
        slide_id = int(selected_slide.SlideID)
        selection = window.Selection
        selection_type = int(selection.Type)
        if selection_type not in {PPT_SELECTION_SHAPES, PPT_SELECTION_TEXT}:
            raise AppActionBlocked("PowerPoint에서 편집할 Shape 또는 텍스트를 선택해주세요.")
        shape_range = selection.ShapeRange
        if int(shape_range.Count) != 1:
            raise AppActionBlocked("PowerPoint Shape는 한 번에 하나만 편집할 수 있습니다.")
        selection_shape = self._item(shape_range, 1)
        shape_id = int(selection_shape.Id)
        selection_shape = shape_range = selected_slide = None
        # ShapeRange proxies are selection-scoped and can disconnect during a
        # rapid sequence. Reacquire the exact slide and Shape by stable IDs.
        slide = self._slide_by_id(presentation, slide_id, slide_index)
        shape = self._shape_by_id(slide, shape_id)
        shape_name = str(shape.Name or "")
        has_text_frame = self._has_text_frame(shape)
        full_text = ""
        selected_text = ""
        text_start = 0
        text_length = 0
        style = None
        if has_text_frame:
            full_range = shape.TextFrame.TextRange
            full_text = str(full_range.Text or "")
            if len(full_text) > MAX_PPT_TEXT_CHARS:
                raise AppActionBlocked(
                    f"PowerPoint Shape 텍스트가 {MAX_PPT_TEXT_CHARS:,}자를 넘어 편집하지 않습니다."
                )
            if selection_type == PPT_SELECTION_TEXT:
                selection_range = selection.TextRange
                text_start = int(selection_range.Start)
                text_length = int(selection_range.Length)
                selected_range = full_range.Characters(text_start, text_length)
                selected_text = str(selected_range.Text or "")
                selection_range = None
            else:
                selected_range = full_range
                text_start = 1
                text_length = int(full_range.Length)
                selected_text = full_text
            style = self._style_state(selected_range)
        placeholder_type = None
        try:
            if int(shape.Type) == MSO_PLACEHOLDER:
                placeholder_type = int(shape.PlaceholderFormat.Type)
        except Exception:
            placeholder_type = None
        geometry = self._geometry_state(slide, shape_id)
        state = {
            "document_id": office_document_id(presentation),
            "document_name": str(presentation.Name),
            "slide_index": slide_index,
            "slide_id": slide_id,
            "shape_id": shape_id,
            "shape_name": shape_name,
            "selection_type": selection_type,
            "has_text_frame": has_text_frame,
            "full_text": full_text,
            "full_text_digest": self._digest(full_text),
            "selected_text": selected_text,
            "selected_text_digest": self._digest(selected_text),
            "text_start": text_start,
            "text_length": text_length,
            "style": style,
            **geometry,
            "placeholder_type": placeholder_type,
            "saved": int(getattr(presentation, "Saved", 0)) != 0,
        }
        return slide, self._shape_by_id(slide, shape_id), state

    @staticmethod
    def _target(state):
        role = (
            f"placeholder:{state['placeholder_type']}"
            if state["placeholder_type"] is not None
            else state["shape_name"]
        )
        return f"슬라이드 {state['slide_index']} · {role} · Shape {state['shape_id']}"

    @staticmethod
    def _base_snapshot(state, operation):
        return {
            "document_id": state["document_id"],
            "operation": operation,
            "slide_id": state["slide_id"],
            "slide_index": state["slide_index"],
            "shape_id": state["shape_id"],
            "selection_type": state["selection_type"],
            "text_start": state["text_start"],
            "text_length": state["text_length"],
            "selected_text_digest": state["selected_text_digest"],
            "full_text_digest": state["full_text_digest"],
            "geometry": {
                "left": state["left"],
                "top": state["top"],
                "width": state["width"],
                "height": state["height"],
                "rotation": state["rotation"],
            },
            "style": state["style"],
            "placeholder_type": state["placeholder_type"],
        }

    @staticmethod
    def _common_current_state(state):
        return {
            "slide_number": state["slide_index"],
            "slide_id": state["slide_id"],
            "shape_id": state["shape_id"],
            "shape_name": state["shape_name"],
            "selection_type": state["selection_type"],
            "selected_length": len(state["selected_text"]),
            "selected_digest": state["selected_text_digest"],
            "selected_preview": state["selected_text"][:120],
            "placeholder_type": state["placeholder_type"],
            "style": state["style"],
            "geometry": {
                "left": state["left"],
                "top": state["top"],
                "width": state["width"],
                "height": state["height"],
            },
        }

    @staticmethod
    def _text_range(shape, state):
        full = shape.TextFrame.TextRange
        if state["selection_type"] == PPT_SELECTION_TEXT:
            return full.Characters(state["text_start"], state["text_length"])
        return full


    @contextmanager
    def _presentation_session(self, path):
        """Open the exact connected presentation once for one operation."""
        with exact_office_document(
            "powerpoint",
            path,
            application_getter=self._application_getter,
            require_visible=self._require_visible,
            com_runtime=self._com_runtime,
        ) as (application, presentation):
            yield PowerPointSession(application, presentation)

    def prepare(self, operation, params):
        operation_module = POWERPOINT_OPERATIONS.require(operation)
        if not isinstance(params, dict):
            raise AppActionBlocked("PowerPoint 작업의 params는 객체 형식이어야 합니다.")
        path = self._document_path(params)

        def prepare_once():
            with self._presentation_session(path) as session:
                return operation_module.prepare(self, session, params)

        return self._retry_transient_com(
            prepare_once,
            include_proxy_reacquire=True,
        )

    @staticmethod
    def _ensure_same_context(current, prepared):
        if current.context_fingerprint != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인 이후 PowerPoint 슬라이드·Shape·텍스트 또는 서식이 바뀌어 실행하지 않았습니다."
            )

    @staticmethod
    def _result(prepared, changed, observations):
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "powerpoint",
            "operation": prepared.operation,
            "document_id": prepared.document_id,
            "target": prepared.target,
            "changed": bool(changed),
            **dict(observations),
        }

    def execute(self, prepared):
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        operation_module = POWERPOINT_OPERATIONS.require_prepared(prepared)
        path = prepared.params.get("document_path")
        # The session carries the write flag, and it is read on the exception
        # path, so it must outlive the attempt that raised. Reset per attempt:
        # a retry that never reached its write may still be retried.
        attempt = {"session": None}

        def execute_once():
            attempt["session"] = None
            with self._presentation_session(path) as session:
                attempt["session"] = session
                return operation_module.execute(self, session, prepared)

        def write_started():
            session = attempt["session"]
            return bool(session is not None and session.write_started)

        return self._retry_transient_com(
            execute_once,
            include_proxy_reacquire=True,
            write_started=write_started,
        )

    def _range_for_prepared(self, shape, prepared):
        full = shape.TextFrame.TextRange
        if prepared.params.get("selection_type") == PPT_SELECTION_TEXT:
            return full.Characters(
                prepared.params["text_start"],
                prepared.params["text_length"],
            )
        return full


    @staticmethod
    def _apply_font(target, values):
        if values.get("bold") is not None:
            target.Font.Bold = int(values["bold"])
        if values.get("font_size") is not None:
            target.Font.Size = float(values["font_size"])


    @classmethod
    def _set_shape_position(cls, slide, shape_id, left, top):
        shape = cls._shape_by_id(slide, shape_id)
        shape.Left = float(left)
        shape.Top = float(top)
        shape = None

    @classmethod
    def _set_shape_size(cls, slide, shape_id, width, height, lock):
        shape = cls._shape_by_id(slide, shape_id)
        try:
            shape.LockAspectRatio = 0
            shape.Width = float(width)
            shape.Height = float(height)
        finally:
            # Restore the user's aspect-ratio mode even if one size write fails.
            shape.LockAspectRatio = int(lock)
            shape = None

    @staticmethod
    def _position_matches(state, left, top):
        return (
            abs(float(state["left"]) - float(left)) <= 0.05
            and abs(float(state["top"]) - float(top)) <= 0.05
        )

    @staticmethod
    def _size_matches(state, width, height, lock=None):
        matches = (
            abs(float(state["width"]) - float(width)) <= 0.05
            and abs(float(state["height"]) - float(height)) <= 0.05
        )
        return matches and (
            lock is None or int(state["lock_aspect_ratio"]) == int(lock)
        )


    @staticmethod
    def _apply_style(target, style):
        target.Font.Bold = int(style["bold"])
        target.Font.Size = float(style["font_size"])
        if style.get("font_name"):
            target.Font.Name = style["font_name"]
        if style.get("font_color") is not None:
            target.Font.Color.RGB = int(style["font_color"])
        target.ParagraphFormat.Alignment = int(style["alignment"])


    def undo(self, prepared, record=None):
        """Restore one verified PowerPoint Shape/text snapshot."""
        return PowerPointUndoService(self, PPT_SELECTION_SHAPES).execute(
            prepared
        )
