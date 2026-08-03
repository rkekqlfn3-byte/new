"""Safe native actions for one selected PowerPoint shape or text range."""

from __future__ import annotations

import hashlib
import os
import re
import time

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_edit_helpers import (
    exact_office_document,
    office_document_id,
)
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.app_actions.office_undo_services import PowerPointUndoService

MAX_PPT_TEXT_CHARS = 20_000
PPT_SELECTION_SHAPES = 2
PPT_SELECTION_TEXT = 3
MSO_PLACEHOLDER = 14
PPT_ALIGNMENTS = {
    "left": 1,
    "center": 2,
    "right": 3,
    "justify": 4,
}
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


class PowerPointAdapter:
    supported_operations = frozenset(
        {
            "replace_shape_text",
            "set_text_format",
            "set_text_alignment",
            "move_shape",
            "resize_shape",
            "match_previous_style",
        }
    )

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

    def _prepare_replace(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        if not state["has_text_frame"]:
            raise AppActionBlocked("선택한 PowerPoint Shape에는 편집할 텍스트가 없습니다.")
        text = str(params.get("text") if params.get("text") is not None else "")
        if len(text) > MAX_PPT_TEXT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"PowerPoint 텍스트는 최대 {MAX_PPT_TEXT_CHARS:,}자까지 지원합니다."
            )
        noop = text == state["selected_text"]
        snapshot = {
            **self._base_snapshot(state, "replace_shape_text"),
            "requested_digest": self._digest(text),
        }
        return PreparedAction(
            app="powerpoint",
            operation="replace_shape_text",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "text": text,
                "original_text": state["selected_text"],
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=0 if noop else max(len(text), len(state["selected_text"])),
            destructive=not noop,
            reversible=True,
            verification_method="read_powerpoint_shape_text",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    @staticmethod
    def _uniform_style_value(value, label):
        if value is None:
            raise AppActionBlocked(f"PowerPoint {label}을 읽지 못했습니다.")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise AppActionBlocked(f"PowerPoint {label}을 읽지 못했습니다.") from error
        if number <= -2 or abs(number) >= 9_999_999:
            raise AppActionBlocked(
                f"선택 텍스트의 PowerPoint {label}이 서로 달라 변경하지 않습니다."
            )
        return number

    def _desired_text_format(self, params, state):
        if state["style"] is None:
            raise AppActionBlocked("선택한 Shape에 텍스트 서식을 적용할 수 없습니다.")
        prepared_desired = params.get("desired")
        if isinstance(prepared_desired, dict):
            desired = {}
            if prepared_desired.get("bold") is not None:
                self._uniform_style_value(state["style"]["bold"], "굵기")
                desired["bold"] = -1 if int(prepared_desired["bold"]) != 0 else 0
            if prepared_desired.get("font_size") is not None:
                self._uniform_style_value(state["style"]["font_size"], "글자 크기")
                size = float(prepared_desired["font_size"])
                if not 1 <= size <= 4000:
                    raise AppActionBlocked("PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
                desired["font_size"] = size
            if desired:
                return desired
        desired = {}
        if params.get("bold") is not None:
            self._uniform_style_value(state["style"]["bold"], "굵기")
            desired["bold"] = -1 if bool(params["bold"]) else 0
        if params.get("font_size") is not None:
            self._uniform_style_value(state["style"]["font_size"], "글자 크기")
            size = float(params["font_size"])
            if not 1 <= size <= 4000:
                raise AppActionBlocked("PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        elif params.get("font_size_delta") is not None:
            current = self._uniform_style_value(
                state["style"]["font_size"],
                "글자 크기",
            )
            size = current + float(params["font_size_delta"])
            if not 1 <= size <= 4000:
                raise AppActionBlocked("변경할 PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        if not desired:
            raise AppActionBlocked("변경할 PowerPoint 글자 서식을 지정해주세요.")
        return desired

    def _prepare_text_format(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        desired = self._desired_text_format(params, state)
        current = {
            "bold": state["style"]["bold"],
            "font_size": state["style"]["font_size"],
        }
        noop = all(current[key] == value for key, value in desired.items())
        snapshot = {
            **self._base_snapshot(state, "set_text_format"),
            "desired": desired,
        }
        return PreparedAction(
            app="powerpoint",
            operation="set_text_format",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "desired": desired,
                "original": current,
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=0 if noop else max(1, len(state["selected_text"])),
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_font",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    @staticmethod
    def _normalize_alignment(value):
        numeric = {
            1: ("left", 1),
            2: ("center", 2),
            3: ("right", 3),
            4: ("justify", 4),
        }
        try:
            if int(value) in numeric and str(value).strip() == str(int(value)):
                return numeric[int(value)]
        except (TypeError, ValueError):
            pass
        text = str(value or "").strip().casefold()
        aliases = {
            "왼쪽": "left",
            "가운데": "center",
            "중앙": "center",
            "오른쪽": "right",
            "양쪽": "justify",
        }
        text = aliases.get(text, text)
        if text not in PPT_ALIGNMENTS:
            raise AppActionBlocked(
                "PowerPoint 정렬은 왼쪽·가운데·오른쪽·양쪽을 지원합니다."
            )
        return text, PPT_ALIGNMENTS[text]

    def _prepare_alignment(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        if state["style"] is None:
            raise AppActionBlocked("선택한 Shape에 문단 정렬을 적용할 수 없습니다.")
        self._uniform_style_value(state["style"]["alignment"], "문단 정렬")
        label, alignment = self._normalize_alignment(params.get("alignment"))
        current = int(state["style"]["alignment"])
        noop = current == alignment
        snapshot = {
            **self._base_snapshot(state, "set_text_alignment"),
            "desired_alignment": alignment,
        }
        return PreparedAction(
            app="powerpoint",
            operation="set_text_alignment",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "alignment": alignment,
                "alignment_label": label,
                "original_alignment": current,
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=0 if noop else 1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_paragraph_alignment",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def _prepare_move(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        if params.get("left") is not None and params.get("top") is not None:
            left = float(params["left"])
            top = float(params["top"])
            dx = left - state["left"]
            dy = top - state["top"]
        else:
            dx = float(params.get("dx", 0))
            dy = float(params.get("dy", 0))
            left = state["left"] + dx
            top = state["top"] + dy
        if (dx == 0 and dy == 0) or abs(dx) > 500 or abs(dy) > 500:
            raise AppActionBlocked("PowerPoint Shape 이동량은 -500~500pt 범위여야 합니다.")
        slide_width = float(presentation.PageSetup.SlideWidth)
        slide_height = float(presentation.PageSetup.SlideHeight)
        if (
            left < 0
            or top < 0
            or left + state["width"] > slide_width
            or top + state["height"] > slide_height
        ):
            raise AppActionBlocked("Shape가 슬라이드 밖으로 나가므로 이동하지 않았습니다.")
        snapshot = {
            **self._base_snapshot(state, "move_shape"),
            "desired_left": left,
            "desired_top": top,
        }
        return PreparedAction(
            app="powerpoint",
            operation="move_shape",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "left": left,
                "top": top,
                "original_left": state["left"],
                "original_top": state["top"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_shape_position",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
        )

    def _prepare_resize(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        if params.get("width") is not None and params.get("height") is not None:
            width = float(params["width"])
            height = float(params["height"])
            width_scale = width / state["width"] if state["width"] else 0
            height_scale = height / state["height"] if state["height"] else 0
            if not 0.5 <= width_scale <= 2 or not 0.5 <= height_scale <= 2:
                raise AppActionBlocked(
                    "PowerPoint Shape 크기 배율은 0.5~2 사이여야 합니다."
                )
        else:
            scale = float(params.get("scale", 1))
            if not 0.5 <= scale <= 2 or abs(scale - 1) < 0.0001:
                raise AppActionBlocked(
                    "PowerPoint Shape 크기 배율은 0.5~2 사이여야 합니다."
                )
            width = state["width"] * scale
            height = state["height"] * scale
        slide_width = float(presentation.PageSetup.SlideWidth)
        slide_height = float(presentation.PageSetup.SlideHeight)
        if state["left"] + width > slide_width or state["top"] + height > slide_height:
            raise AppActionBlocked("크기를 바꾸면 Shape가 슬라이드 밖으로 나갑니다.")
        snapshot = {
            **self._base_snapshot(state, "resize_shape"),
            "desired_width": width,
            "desired_height": height,
        }
        return PreparedAction(
            app="powerpoint",
            operation="resize_shape",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "width": width,
                "height": height,
                "original_width": state["width"],
                "original_height": state["height"],
                "lock_aspect_ratio": state["lock_aspect_ratio"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_shape_size",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
        )

    @staticmethod
    def _role_name(name):
        return re.sub(r"[\s_-]*\d+$", "", str(name or "").strip().casefold())

    def _reference_shape(self, presentation, state):
        if state["slide_index"] <= 1:
            raise AppActionBlocked("앞 슬라이드가 없어 스타일을 참조할 수 없습니다.")
        slide = presentation.Slides.Item(state["slide_index"] - 1)
        matches = []
        for index in range(1, int(slide.Shapes.Count) + 1):
            shape = slide.Shapes.Item(index)
            placeholder = None
            try:
                if int(shape.Type) == MSO_PLACEHOLDER:
                    placeholder = int(shape.PlaceholderFormat.Type)
            except Exception:
                pass
            same_role = (
                state["placeholder_type"] is not None
                and placeholder == state["placeholder_type"]
            ) or (
                state["placeholder_type"] is None
                and self._role_name(shape.Name) == self._role_name(state["shape_name"])
            )
            if same_role and self._has_text_frame(shape):
                matches.append(shape)
        if len(matches) != 1:
            raise AppActionBlocked(
                "앞 슬라이드에서 같은 역할의 텍스트 Shape를 하나로 특정하지 못했습니다."
            )
        return slide, matches[0]

    def _prepare_match_style(self, application, presentation, params):
        _, _, state = self._context(application, presentation)
        if state["style"] is None:
            raise AppActionBlocked("현재 Shape에 참조 스타일을 적용할 텍스트가 없습니다.")
        source_slide, source_shape = self._reference_shape(presentation, state)
        source_range = source_shape.TextFrame.TextRange
        desired = self._style_state(source_range)
        original = dict(state["style"])
        noop = original == desired
        snapshot = {
            **self._base_snapshot(state, "match_previous_style"),
            "source_slide_id": int(source_slide.SlideID),
            "source_shape_id": int(source_shape.Id),
            "desired_style": desired,
        }
        return PreparedAction(
            app="powerpoint",
            operation="match_previous_style",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "desired": desired,
                "original": original,
                "source_slide_id": int(source_slide.SlideID),
                "source_shape_id": int(source_shape.Id),
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=self._common_current_state(state),
            estimated_changes=0 if noop else max(1, len(state["selected_text"])),
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_style",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def _prepare_in_document(self, application, presentation, operation, params):
        if operation == "replace_shape_text":
            return self._prepare_replace(application, presentation, params)
        if operation == "set_text_format":
            return self._prepare_text_format(application, presentation, params)
        if operation == "set_text_alignment":
            return self._prepare_alignment(application, presentation, params)
        if operation == "move_shape":
            return self._prepare_move(application, presentation, params)
        if operation == "resize_shape":
            return self._prepare_resize(application, presentation, params)
        return self._prepare_match_style(application, presentation, params)

    def prepare(self, operation, params):
        operation = str(operation or "").strip()
        if operation not in self.supported_operations:
            raise AppActionBlocked(
                f"아직 지원하지 않는 PowerPoint 작업입니다: {operation}"
            )
        if not isinstance(params, dict):
            raise AppActionBlocked("PowerPoint 작업의 params는 객체 형식이어야 합니다.")
        path = self._document_path(params)
        def prepare_once():
            with exact_office_document(
                "powerpoint",
                path,
                application_getter=self._application_getter,
                require_visible=self._require_visible,
                com_runtime=self._com_runtime,
            ) as (application, presentation):
                return self._prepare_in_document(
                    application,
                    presentation,
                    operation,
                    params,
                )

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
        if (
            prepared.app != "powerpoint"
            or prepared.operation not in self.supported_operations
        ):
            raise AppActionBlocked(
                "지원되는 PowerPoint PreparedAction만 실행할 수 있습니다."
            )
        path = prepared.params.get("document_path")
        write_state = {"started": False}

        def execute_once():
            write_state["started"] = False
            with exact_office_document(
                "powerpoint",
                path,
                application_getter=self._application_getter,
                require_visible=self._require_visible,
                com_runtime=self._com_runtime,
            ) as (application, presentation):
                current = self._prepare_in_document(
                    application,
                    presentation,
                    prepared.operation,
                    prepared.params,
                )
                self._ensure_same_context(current, prepared)
                if current.noop:
                    return self._result(current, False, {"noop": True})
                slide = self._slide_by_id(
                    presentation,
                    current.current_state["slide_id"],
                    current.current_state["slide_number"],
                )
                shape_id = current.current_state["shape_id"]
                shape = self._shape_by_id(slide, shape_id)
                write_state["started"] = True
                if prepared.operation == "replace_shape_text":
                    return self._execute_replace(shape, current)
                if prepared.operation == "set_text_format":
                    return self._execute_text_format(shape, current)
                if prepared.operation == "set_text_alignment":
                    return self._execute_alignment(shape, current)
                if prepared.operation == "move_shape":
                    return self._execute_move(slide, shape_id, current)
                if prepared.operation == "resize_shape":
                    return self._execute_resize(slide, shape_id, current)
                return self._execute_style(shape, current)

        return self._retry_transient_com(
            execute_once,
            include_proxy_reacquire=True,
            write_started=lambda: write_state["started"],
        )

    def _range_for_prepared(self, shape, prepared):
        full = shape.TextFrame.TextRange
        if prepared.params.get("selection_type") == PPT_SELECTION_TEXT:
            return full.Characters(
                prepared.params["text_start"],
                prepared.params["text_length"],
            )
        return full

    def _execute_replace(self, shape, prepared):
        target = self._range_for_prepared(shape, prepared)
        text = prepared.params["text"]
        original = prepared.params["original_text"]
        start = int(prepared.params["text_start"])
        whole_shape = prepared.params["selection_type"] == PPT_SELECTION_SHAPES
        try:
            target.Text = text
            full = shape.TextFrame.TextRange
            actual = (
                str(full.Text or "")
                if whole_shape
                else str(full.Characters(start, len(text)).Text or "")
            )
            if actual != text:
                raise AppActionVerificationError(
                    "PowerPoint Shape 텍스트 수정 결과가 요청과 다릅니다."
                )
            if not whole_shape:
                full.Characters(start, len(text)).Select()
        except Exception as error:
            try:
                full = shape.TextFrame.TextRange
                if whole_shape:
                    full.Text = original
                else:
                    full.Characters(start, len(text)).Text = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 텍스트 수정 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"text_length": len(text)})

    @staticmethod
    def _apply_font(target, values):
        if values.get("bold") is not None:
            target.Font.Bold = int(values["bold"])
        if values.get("font_size") is not None:
            target.Font.Size = float(values["font_size"])

    def _execute_text_format(self, shape, prepared):
        target = self._range_for_prepared(shape, prepared)
        desired = prepared.params["desired"]
        try:
            self._apply_font(target, desired)
            if "bold" in desired and int(target.Font.Bold) != int(desired["bold"]):
                raise AppActionVerificationError("PowerPoint 굵기 적용 결과가 다릅니다.")
            if "font_size" in desired and abs(
                float(target.Font.Size) - float(desired["font_size"])
            ) > 0.01:
                raise AppActionVerificationError(
                    "PowerPoint 글자 크기 적용 결과가 다릅니다."
                )
        except Exception as error:
            try:
                self._apply_font(target, prepared.params["original"])
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"format": desired})

    def _execute_alignment(self, shape, prepared):
        target = self._range_for_prepared(shape, prepared)
        desired = int(prepared.params["alignment"])
        try:
            target.ParagraphFormat.Alignment = desired
            if int(target.ParagraphFormat.Alignment) != desired:
                raise AppActionVerificationError(
                    "PowerPoint 텍스트 정렬 결과가 다릅니다."
                )
        except Exception as error:
            try:
                target.ParagraphFormat.Alignment = int(
                    prepared.params["original_alignment"]
                )
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 텍스트 정렬 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"alignment": desired})

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

    def _execute_move(self, slide, shape_id, prepared):
        try:
            self._set_shape_position(
                slide,
                shape_id,
                prepared.params["left"],
                prepared.params["top"],
            )
            actual = self._geometry_state(slide, shape_id)
            if not self._position_matches(
                actual, prepared.params["left"], prepared.params["top"]
            ):
                raise AppActionVerificationError("PowerPoint Shape 이동 결과가 다릅니다.")
            self._shape_by_id(slide, shape_id).Select()
        except Exception as error:
            restored = False
            try:
                self._set_shape_position(
                    slide,
                    shape_id,
                    prepared.params["original_left"],
                    prepared.params["original_top"],
                )
                restored = self._position_matches(
                    self._geometry_state(slide, shape_id),
                    prepared.params["original_left"],
                    prepared.params["original_top"],
                )
            except Exception:
                restored = False
            if not restored:
                raise AppActionVerificationError(
                    "PowerPoint Shape 이동 실패 뒤 원래 위치 복원을 확인하지 못했습니다."
                ) from None
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint Shape 이동 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            prepared,
            True,
            {"left": actual["left"], "top": actual["top"]},
        )

    def _execute_resize(self, slide, shape_id, prepared):
        original_lock = int(prepared.params["lock_aspect_ratio"])
        try:
            self._set_shape_size(
                slide,
                shape_id,
                prepared.params["width"],
                prepared.params["height"],
                original_lock,
            )
            actual = self._geometry_state(slide, shape_id)
            if not self._size_matches(
                actual,
                prepared.params["width"],
                prepared.params["height"],
                original_lock,
            ):
                raise AppActionVerificationError("PowerPoint Shape 크기 결과가 다릅니다.")
            self._shape_by_id(slide, shape_id).Select()
        except Exception as error:
            restored = False
            try:
                self._set_shape_size(
                    slide,
                    shape_id,
                    prepared.params["original_width"],
                    prepared.params["original_height"],
                    original_lock,
                )
                restored = self._size_matches(
                    self._geometry_state(slide, shape_id),
                    prepared.params["original_width"],
                    prepared.params["original_height"],
                    original_lock,
                )
            except Exception:
                restored = False
            if not restored:
                raise AppActionVerificationError(
                    "PowerPoint Shape 크기 변경 실패 뒤 원래 크기 복원을 확인하지 못했습니다."
                ) from None
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint Shape 크기 변경 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            prepared,
            True,
            {"width": actual["width"], "height": actual["height"]},
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

    def _execute_style(self, shape, prepared):
        target = self._range_for_prepared(shape, prepared)
        desired = prepared.params["desired"]
        try:
            self._apply_style(target, desired)
            actual = self._style_state(target)
            if actual != desired:
                raise AppActionVerificationError(
                    "PowerPoint 앞 슬라이드 스타일 적용 결과가 다릅니다."
                )
        except Exception as error:
            try:
                self._apply_style(target, prepared.params["original"])
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 참조 스타일 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"style": desired})

    def undo(self, prepared, record=None):
        """Restore one verified PowerPoint Shape/text snapshot."""
        return PowerPointUndoService(self, PPT_SELECTION_SHAPES).execute(
            prepared
        )
