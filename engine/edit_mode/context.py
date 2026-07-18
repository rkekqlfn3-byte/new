"""Read-only, COM-free context snapshots for connected edit documents."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from engine.app_actions.com_lifecycle import com_apartment
from engine.edit_mode.native_bridge import (
    APP_PROGIDS,
    NativeDocumentBridge,
    NativeOfficeBusy,
    _com_value,
    _is_office_busy_error,
    _item,
    _rot_office_reference,
    _same_path,
    excel_reference_for_identity,
)
from engine.edit_mode.session import (
    canonical_document_path,
    document_identity_fingerprint,
    runtime_document_identity_fingerprint,
)
from engine.edit_mode.text_tone import classify_text_tone


EDIT_CONTEXT_SCHEMA_VERSION = 1
MAX_CONTEXT_PREVIEW_CHARS = 240
MAX_EXCEL_PREVIEW_CELLS = 25
_WHITESPACE = re.compile(r"[\r\n\t ]+")


class EditContextError(RuntimeError):
    """The connected document cannot provide a trustworthy current context."""

    error_type = "validation_error"
    status = "stale_context"


class EditContextUnavailable(EditContextError):
    error_type = "target_not_found"


class EditContextInactive(EditContextError):
    pass


class EditContextBusy(EditContextError):
    error_type = "environment_error"
    status = "busy"
    retryable = True


class EditContextUnsupported(EditContextError):
    status = "blocked"


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _string(value) -> str:
    try:
        return str(_com_value(value) or "")
    except Exception:
        return ""


def _integer(value, default=0) -> int:
    try:
        return int(_com_value(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _boolean(value, default=False) -> bool:
    try:
        return bool(_com_value(value))
    except Exception:
        return bool(default)


def _preview(value: str) -> str:
    normalized = _WHITESPACE.sub(" ", str(value or "")).strip(" \x07")
    if len(normalized) <= MAX_CONTEXT_PREVIEW_CHARS:
        return normalized
    return normalized[: MAX_CONTEXT_PREVIEW_CHARS - 1].rstrip() + "…"


def _text_digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()


def _json_copy(value, label: str):
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise EditContextError(f"{label}에는 직렬화 가능한 값만 사용할 수 있습니다.") from error
    return json.loads(encoded)


def _flatten_excel_values(value) -> list[str]:
    values = []

    def visit(item):
        if len(values) >= MAX_EXCEL_PREVIEW_CELLS:
            return
        if isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
            return
        if item is not None and str(item) != "":
            values.append(str(item))

    visit(value)
    return values


@dataclass(frozen=True)
class EditContext:
    """One JSON-only snapshot of the user's current native-app focus."""

    session_id: str
    app_type: str
    file_path: str
    document_name: str
    document_fingerprint: str
    context_fingerprint: str
    active_container: str | None
    selection_reference: str | None
    selection_kind: str
    target: dict[str, Any]
    selected_text_preview: str
    selected_text_length: int
    selected_text_digest: str
    cursor_reference: str | None
    read_only: bool
    modified: bool
    captured_at: str
    # Coarse ending-based tone label; excluded from the context fingerprint
    # because the digest already identifies the text itself.
    selected_text_tone: str = "unknown"
    schema_version: int = EDIT_CONTEXT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(asdict(self))


@runtime_checkable
class ContextProvider(Protocol):
    app_type: str

    def capture(self, session: Mapping[str, Any]) -> Mapping[str, Any]: ...


class NativeDocumentContextReader:
    """Acquire native references only for the duration of one read callback."""

    def __init__(
        self,
        com_runtime=None,
        *,
        busy_retry_attempts=8,
        busy_retry_delay=0.15,
    ):
        self._com_runtime = com_runtime
        self._busy_retry_attempts = max(1, min(int(busy_retry_attempts), 20))
        self._busy_retry_delay = max(0.0, min(float(busy_retry_delay), 0.5))

    @staticmethod
    def _active_office_document(application, app_type):
        if app_type == "excel":
            return getattr(application, "ActiveWorkbook", None)
        if app_type == "word":
            return getattr(application, "ActiveDocument", None)
        return getattr(application, "ActivePresentation", None)

    def capture(self, app_type: str, expected_path: str) -> dict[str, Any]:
        normalized = str(app_type or "").strip().casefold()
        with com_apartment(self._com_runtime):
            if normalized == "hwp":
                return self._capture_hwp_document(expected_path)
            if normalized in {"excel", "word", "powerpoint"}:
                last_busy = None
                for attempt in range(self._busy_retry_attempts):
                    try:
                        return self._capture_office_document(normalized, expected_path)
                    except NativeOfficeBusy as error:
                        last_busy = error
                    except Exception as error:
                        if not _is_office_busy_error(error):
                            raise
                        last_busy = error
                    if attempt + 1 < self._busy_retry_attempts:
                        try:
                            import pythoncom

                            pythoncom.PumpWaitingMessages()
                        except Exception:
                            pass
                        time.sleep(self._busy_retry_delay)
                app_label = {
                    "excel": "Excel",
                    "word": "Word",
                    "powerpoint": "PowerPoint",
                }.get(normalized, "Office")
                raise EditContextBusy(
                    f"{app_label}이 문서 입력 중이거나 대화상자를 처리 중이라 "
                    "현재 선택 영역을 읽지 못했습니다. 입력을 완료한 뒤 다시 시도해주세요."
                ) from last_busy
        raise EditContextUnsupported(f"지원하지 않는 편집 문맥 앱입니다: {app_type}")

    def capture_session(
        self,
        app_type: str,
        session: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Capture a saved path or one process-local unsaved Excel identity."""
        current = dict(session or {})
        normalized = str(app_type or "").strip().casefold()
        if str(current.get("identity_kind") or "file") != "runtime":
            return self.capture(normalized, str(current.get("file_path") or ""))
        if normalized != "excel":
            raise EditContextUnsupported(
                "미저장 문서 문맥은 현재 Excel만 지원합니다."
            )
        with com_apartment(self._com_runtime):
            last_busy = None
            for attempt in range(self._busy_retry_attempts):
                try:
                    return self._capture_runtime_excel_document(current)
                except NativeOfficeBusy as error:
                    last_busy = error
                except Exception as error:
                    if not _is_office_busy_error(error):
                        raise
                    last_busy = error
                if attempt + 1 < self._busy_retry_attempts:
                    try:
                        import pythoncom

                        pythoncom.PumpWaitingMessages()
                    except Exception:
                        pass
                    time.sleep(self._busy_retry_delay)
            raise EditContextBusy(
                "Excel이 입력 중이거나 대화상자를 처리 중이라 미저장 문서를 "
                "확인하지 못했습니다. 입력을 완료한 뒤 다시 시도해주세요."
            ) from last_busy

    def _capture_runtime_excel_document(
        self,
        session: Mapping[str, Any],
    ) -> dict[str, Any]:
        application = document = metadata = None
        try:
            application, document, metadata = excel_reference_for_identity(
                window_handle=int(session.get("window_handle") or 0),
                runtime_document_id=str(
                    session.get("runtime_document_id") or ""
                ),
                document_name=str(session.get("document_name") or ""),
            )
            if application is None or document is None or metadata is None:
                raise EditContextInactive(
                    "처음 연결한 미저장 Excel 통합문서를 현재 창에서 찾지 못했습니다. "
                    "다시 연결해주세요."
                )
            result = self._capture_excel(application, document)
            result.update({
                "runtime_document_id": str(
                    session.get("runtime_document_id") or ""
                ).strip().upper(),
                "window_handle": metadata.get("window_handle"),
                "is_saved": metadata.get("is_saved", False),
                "identity_kind": metadata.get("identity_kind", "runtime"),
            })
            return result
        finally:
            metadata = document = application = None

    def _capture_office_document(self, app_type: str, expected_path: str) -> dict[str, Any]:
        application = None
        document = None
        active = None
        busy_error = None
        try:
            import win32com.client

            try:
                application = win32com.client.GetActiveObject(APP_PROGIDS[app_type])
                document = NativeDocumentBridge._office_document(
                    application, app_type, expected_path
                )
            except Exception as error:
                if _is_office_busy_error(error):
                    busy_error = error
                application = None
                document = None
            if document is None:
                try:
                    application, document = _rot_office_reference(expected_path)
                except NativeOfficeBusy as error:
                    busy_error = error
            if document is None or application is None:
                if busy_error is not None:
                    raise NativeOfficeBusy(str(busy_error)) from busy_error
                raise EditContextUnavailable(
                    "연결된 문서가 닫혔거나 네이티브 앱에서 다시 찾을 수 없습니다."
                )
            active = self._active_office_document(application, app_type)
            active_path = (
                _string(getattr(active, "FullName", ""))
                if active is not None
                else ""
            )
            if not _same_path(active_path, expected_path):
                raise EditContextInactive(
                    "연결된 문서가 현재 활성 문서가 아닙니다. 해당 문서 창을 선택해주세요."
                )
            if app_type == "excel":
                return self._capture_excel(application, document)
            if app_type == "word":
                return self._capture_word(application, document)
            return self._capture_powerpoint(application, document)
        except EditContextError:
            raise
        except NativeOfficeBusy:
            raise
        except Exception as error:
            if _is_office_busy_error(error):
                raise NativeOfficeBusy(str(error)) from error
            raise EditContextUnavailable(
                "연결된 Office 문서의 현재 선택 영역을 읽지 못했습니다."
            ) from error
        finally:
            active = None
            document = None
            application = None

    @staticmethod
    def _capture_excel(application, document) -> dict[str, Any]:
        sheet = getattr(application, "ActiveSheet", None)
        selection = getattr(application, "Selection", None)
        sheet_name = _string(getattr(sheet, "Name", "")) or None
        selection_reference = None
        selection_kind = "none"
        target: dict[str, Any] = {"sheet_name": sheet_name}
        selected_text = ""

        if selection is not None:
            try:
                address_value = getattr(selection, "Address")
                address = (
                    _string(address_value(False, False))
                    if callable(address_value)
                    else _string(address_value)
                )
                selection_reference = address.replace("$", "") or None
            except Exception:
                selection_reference = None
            if selection_reference:
                selection_kind = "range"
                target["address"] = selection_reference
                cell_count = _integer(
                    getattr(selection, "CountLarge", getattr(selection, "Count", 0))
                )
                target["cell_count"] = max(0, cell_count)
                if 0 < cell_count <= MAX_EXCEL_PREVIEW_CELLS:
                    try:
                        selected_text = " · ".join(
                            _flatten_excel_values(_com_value(selection.Value2))
                        )
                    except Exception:
                        selected_text = ""
            else:
                try:
                    shape_range = getattr(selection, "ShapeRange")
                    shape = _item(shape_range, 1)
                    shape_name = _string(getattr(shape, "Name", ""))
                    shape_id = _integer(getattr(shape, "Id", 0))
                    selection_kind = "shape"
                    selection_reference = shape_name or (
                        f"shape:{shape_id}" if shape_id else "shape"
                    )
                    target.update({"shape_id": shape_id, "shape_name": shape_name})
                except Exception:
                    selection_kind = "object"
                    selection_reference = _string(getattr(selection, "Name", "")) or None

        metadata = NativeDocumentBridge._office_metadata(
            "excel",
            application,
            document,
        )
        return {
            "app_type": "excel",
            "file_path": str(metadata.get("file_path") or ""),
            "document_name": _string(getattr(document, "Name", "")),
            "active_container": sheet_name,
            "selection_reference": selection_reference,
            "selection_kind": selection_kind,
            "target": target,
            "selected_text": selected_text,
            "cursor_reference": selection_reference,
            "read_only": _boolean(getattr(document, "ReadOnly", False)),
            "modified": not _boolean(getattr(document, "Saved", True), True),
            "runtime_document_id": metadata.get("runtime_document_id"),
            "window_handle": metadata.get("window_handle"),
            "is_saved": metadata.get("is_saved", False),
            "identity_kind": metadata.get("identity_kind", "runtime"),
        }

    @staticmethod
    def _capture_word(application, document) -> dict[str, Any]:
        selection = application.Selection
        start = _integer(getattr(selection, "Start", 0))
        end = _integer(getattr(selection, "End", start), start)
        selection_kind = "cursor" if start == end else "text"
        selection_reference = f"{start}:{end}"
        target: dict[str, Any] = {"start": start, "end": end}
        selected_text = _string(getattr(selection, "Text", ""))
        active_container = None

        try:
            page_number = _integer(selection.Information(3))
            if page_number > 0:
                active_container = f"페이지 {page_number}"
                target["page_number"] = page_number
        except Exception:
            pass
        try:
            if _boolean(selection.Information(12)):
                cell = _item(selection.Cells, 1)
                row = _integer(getattr(cell, "RowIndex", 0))
                column = _integer(getattr(cell, "ColumnIndex", 0))
                selection_kind = "table_cell"
                target.update({"table_row": row, "table_column": column})
                try:
                    table_range = _item(selection.Tables, 1).Range
                    target.update({
                        "table_start": _integer(getattr(table_range, "Start", 0)),
                        "table_end": _integer(getattr(table_range, "End", 0)),
                    })
                except Exception:
                    pass
                selection_reference = f"표 R{row}C{column} · {start}:{end}"
        except Exception:
            pass

        try:
            style = getattr(selection, "Style")
            target["style_name"] = (
                _string(getattr(style, "NameLocal", "")) or _string(style)
            )
        except Exception:
            pass
        try:
            font = selection.Font
            target["bold"] = _integer(getattr(font, "Bold", 0))
            target["font_size"] = float(_com_value(getattr(font, "Size", 0)))
        except Exception:
            pass
        try:
            target["paragraph_alignment"] = _integer(
                getattr(selection.ParagraphFormat, "Alignment", 0)
            )
        except Exception:
            pass

        return {
            "app_type": "word",
            "file_path": _string(getattr(document, "FullName", "")),
            "document_name": _string(getattr(document, "Name", "")),
            "active_container": active_container,
            "selection_reference": selection_reference,
            "selection_kind": selection_kind,
            "target": target,
            "selected_text": selected_text,
            "cursor_reference": str(start),
            "read_only": _boolean(getattr(document, "ReadOnly", False)),
            "modified": not _boolean(getattr(document, "Saved", True), True),
        }

    @staticmethod
    def _capture_powerpoint(application, document) -> dict[str, Any]:
        window = application.ActiveWindow
        slide = window.View.Slide
        slide_number = _integer(getattr(slide, "SlideIndex", 0))
        selection = window.Selection
        selection_type = _integer(getattr(selection, "Type", 0))
        target: dict[str, Any] = {
            "slide_number": slide_number,
            "slide_id": _integer(getattr(slide, "SlideID", 0)),
            "selection_type": selection_type,
        }
        selection_kind = {
            0: "none",
            1: "slides",
            2: "shapes",
            3: "text",
        }.get(selection_type, f"selection_{selection_type}")
        selection_reference = None
        selected_text = ""

        if selection_type in {2, 3}:
            try:
                shape_range = selection.ShapeRange
                shape = _item(shape_range, 1)
                shape_id = _integer(getattr(shape, "Id", 0))
                shape_name = _string(getattr(shape, "Name", ""))
                target.update({
                    "shape_id": shape_id,
                    "shape_name": shape_name,
                    "left": float(_com_value(getattr(shape, "Left", 0))),
                    "top": float(_com_value(getattr(shape, "Top", 0))),
                    "width": float(_com_value(getattr(shape, "Width", 0))),
                    "height": float(_com_value(getattr(shape, "Height", 0))),
                    "rotation": float(_com_value(getattr(shape, "Rotation", 0))),
                })
                try:
                    if _integer(getattr(shape, "Type", 0)) == 14:
                        target["placeholder_type"] = _integer(
                            getattr(shape.PlaceholderFormat, "Type", 0)
                        )
                except Exception:
                    pass
                selection_reference = shape_name or (
                    f"shape:{shape_id}" if shape_id else "shape"
                )
                if selection_type == 3:
                    text_range = selection.TextRange
                    text_start = _integer(getattr(text_range, "Start", 0))
                    text_length = _integer(getattr(text_range, "Length", 0))
                    target.update({
                        "text_start": text_start,
                        "text_end": text_start + text_length,
                    })
                    selected_text = _string(getattr(text_range, "Text", ""))
                    selection_reference = (
                        f"{selection_reference} · 텍스트 {text_start}:"
                        f"{text_start + text_length}"
                    )
                else:
                    try:
                        text_frame = shape.TextFrame
                        if _boolean(getattr(text_frame, "HasText", False)):
                            text_range = text_frame.TextRange
                            selected_text = _string(text_range.Text)
                    except Exception:
                        pass
                try:
                    font = text_range.Font
                    paragraph = text_range.ParagraphFormat
                    target.update({
                        "bold": _integer(getattr(font, "Bold", 0)),
                        "font_size": float(_com_value(getattr(font, "Size", 0))),
                        "font_name": _string(getattr(font, "Name", "")),
                        "paragraph_alignment": _integer(
                            getattr(paragraph, "Alignment", 0)
                        ),
                    })
                    try:
                        target["font_color"] = _integer(font.Color.RGB)
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                selection_reference = f"selection:{selection_type}"
        elif selection_type == 1:
            try:
                slide_range = selection.SlideRange
                selected_slides = []
                for index in range(1, _integer(slide_range.Count) + 1):
                    selected_slides.append(_integer(_item(slide_range, index).SlideIndex))
                target["selected_slides"] = selected_slides
                selection_reference = "슬라이드 " + ", ".join(
                    str(number) for number in selected_slides
                )
            except Exception:
                selection_reference = f"슬라이드 {slide_number}"

        return {
            "app_type": "powerpoint",
            "file_path": _string(getattr(document, "FullName", "")),
            "document_name": _string(getattr(document, "Name", "")),
            "active_container": f"슬라이드 {slide_number}" if slide_number else None,
            "selection_reference": selection_reference,
            "selection_kind": selection_kind,
            "target": target,
            "selected_text": selected_text,
            "cursor_reference": selection_reference,
            "read_only": _boolean(getattr(document, "ReadOnly", False)),
            "modified": not _boolean(getattr(document, "Saved", True), True),
        }

    def _capture_hwp_document(self, expected_path: str) -> dict[str, Any]:
        try:
            for hwp in NativeDocumentBridge._hwp_candidates():
                try:
                    documents = hwp.XHwpDocuments
                    document = documents.Active_XHwpDocument
                    full_name = _string(getattr(document, "FullName", ""))
                    if not _same_path(full_name, expected_path):
                        continue
                    selected = tuple(hwp.GetSelectedPos())
                    has_selection = bool(selected[0]) if selected else False
                    coordinates = [
                        _integer(value) for value in selected[1:7]
                    ] if len(selected) >= 7 else []
                    position = [_integer(value) for value in tuple(hwp.GetPos())]
                    selected_text = (
                        _string(hwp.GetTextFile("UNICODE", "saveblock"))
                        if has_selection
                        else ""
                    )
                    reference_values = coordinates if has_selection else position
                    prefix = "selected" if has_selection else "cursor"
                    reference = prefix + ":" + ":".join(
                        str(value) for value in reference_values
                    )
                    edit_mode = _integer(getattr(document, "EditMode", 1), 1)
                    return {
                        "app_type": "hwp",
                        "file_path": full_name,
                        "document_name": Path(full_name).name,
                        "active_container": None,
                        "selection_reference": reference,
                        "selection_kind": "text" if has_selection else "cursor",
                        "target": {
                            "coordinates": coordinates,
                            "position": position,
                        },
                        "selected_text": selected_text,
                        "cursor_reference": ":".join(str(value) for value in position),
                        "read_only": edit_mode == 0,
                        "modified": _boolean(getattr(hwp, "IsModified", False)),
                    }
                finally:
                    hwp = None
        except EditContextError:
            raise
        except Exception as error:
            raise EditContextUnavailable(
                "연결된 한글 문서의 선택 영역을 읽지 못했습니다."
            ) from error
        raise EditContextUnavailable(
            "연결된 한글 문서가 닫혔거나 현재 활성 문서가 아닙니다."
        )


class _NativeContextProvider:
    app_type = ""

    def __init__(self, reader=None):
        self.reader = reader or NativeDocumentContextReader()

    def capture(self, session: Mapping[str, Any]) -> Mapping[str, Any]:
        capture_session = getattr(self.reader, "capture_session", None)
        if callable(capture_session):
            return capture_session(self.app_type, session)
        return self.reader.capture(self.app_type, str(session.get("file_path") or ""))


class ExcelContextProvider(_NativeContextProvider):
    app_type = "excel"


class HwpContextProvider(_NativeContextProvider):
    app_type = "hwp"


class WordContextProvider(_NativeContextProvider):
    app_type = "word"


class PowerPointContextProvider(_NativeContextProvider):
    app_type = "powerpoint"


def default_context_providers(reader=None) -> tuple[ContextProvider, ...]:
    native_reader = reader or NativeDocumentContextReader()
    return (
        ExcelContextProvider(native_reader),
        HwpContextProvider(native_reader),
        WordContextProvider(native_reader),
        PowerPointContextProvider(native_reader),
    )


class EditContextManager:
    """Capture and fingerprint the current selection without retaining COM state."""

    def __init__(self, providers=None, reader=None):
        selected = providers or default_context_providers(reader)
        self._providers = {}
        for provider in selected:
            app_type = str(getattr(provider, "app_type", "")).strip().casefold()
            if not app_type or not callable(getattr(provider, "capture", None)):
                raise EditContextUnsupported("편집 문맥 공급자 구성이 올바르지 않습니다.")
            self._providers[app_type] = provider

    @staticmethod
    def _session_mapping(session) -> dict[str, Any]:
        if isinstance(session, Mapping):
            return dict(session)
        to_dict = getattr(session, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        raise EditContextError("편집 문맥을 읽을 세션 정보가 올바르지 않습니다.")

    def capture(self, session) -> dict[str, Any]:
        current = self._session_mapping(session)
        session_id = str(current.get("session_id") or "").strip()
        app_type = str(current.get("app_type") or "").strip().casefold()
        identity_kind = str(
            current.get("identity_kind") or "file"
        ).strip().casefold()
        runtime_identity = identity_kind == "runtime"
        expected_path = (
            ""
            if runtime_identity
            else canonical_document_path(current.get("file_path"))
        )
        expected_document_fingerprint = str(
            current.get("document_fingerprint") or ""
        ).strip().upper()
        if not session_id or not expected_document_fingerprint:
            raise EditContextError("편집 세션의 문서 식별 정보가 불완전합니다.")
        if runtime_identity:
            actual_document_fingerprint = runtime_document_identity_fingerprint(
                app_type,
                str(current.get("runtime_document_id") or ""),
                int(current.get("window_handle") or 0),
            )
        else:
            actual_document_fingerprint = document_identity_fingerprint(
                expected_path,
                app_type,
            )
        if actual_document_fingerprint != expected_document_fingerprint:
            raise EditContextInactive(
                "연결 후 문서 파일이 교체되어 문맥을 읽지 않았습니다. 다시 연결해주세요."
            )
        provider = self._providers.get(app_type)
        if provider is None:
            raise EditContextUnsupported(f"{app_type} 문맥 공급자가 등록되어 있지 않습니다.")
        try:
            raw = dict(provider.capture(current))
        except EditContextError:
            raise
        except Exception as error:
            raise EditContextUnavailable(
                "연결된 문서의 현재 선택 영역을 읽지 못했습니다."
            ) from error

        captured_app_type = str(raw.get("app_type") or app_type).strip().casefold()
        if captured_app_type != app_type:
            raise EditContextInactive("다른 앱의 문맥이 반환되어 요청을 차단했습니다.")
        if runtime_identity:
            captured_handle = int(raw.get("window_handle") or 0)
            expected_handle = int(current.get("window_handle") or 0)
            captured_runtime_id = str(
                raw.get("runtime_document_id") or ""
            ).strip().upper()
            expected_runtime_id = str(
                current.get("runtime_document_id") or ""
            ).strip().upper()
            if captured_handle != expected_handle:
                raise EditContextInactive(
                    "처음 연결한 Excel 창과 현재 문서 창이 달라 편집을 차단했습니다."
                )
            if captured_runtime_id and captured_runtime_id != expected_runtime_id:
                raise EditContextInactive(
                    "처음 연결한 미저장 통합문서와 현재 통합문서가 달라 편집을 차단했습니다."
                )
            if not captured_runtime_id and str(
                raw.get("document_name") or ""
            ).casefold() != str(current.get("document_name") or "").casefold():
                raise EditContextInactive(
                    "미저장 통합문서 이름이 달라 편집을 차단했습니다. 다시 연결해주세요."
                )
            raw_path = str(raw.get("file_path") or "").strip()
            captured_path = canonical_document_path(raw_path) if raw_path else ""
            expected_path = captured_path
        else:
            captured_path = canonical_document_path(raw.get("file_path"))
            if captured_path != expected_path:
                raise EditContextInactive(
                    "현재 활성 문서가 연결된 문서와 달라 문맥을 읽지 않았습니다."
                )

        selected_text = str(raw.get("selected_text") or "")
        selected_text_digest = _text_digest(selected_text)
        raw_target = raw.get("target") or {}
        if not isinstance(raw_target, Mapping):
            raise EditContextError("편집 문맥의 대상 정보는 객체여야 합니다.")
        target = _json_copy(dict(raw_target), "편집 대상")
        identity = {
            "schema_version": EDIT_CONTEXT_SCHEMA_VERSION,
            "app_type": app_type,
            "file_path": expected_path,
            "document_fingerprint": expected_document_fingerprint,
            "identity_kind": identity_kind,
            "runtime_document_id": str(
                current.get("runtime_document_id") or ""
            ) or None,
            "window_handle": int(current.get("window_handle") or 0),
            "document_saved": bool(expected_path),
            "active_container": str(raw.get("active_container") or "") or None,
            "selection_reference": str(raw.get("selection_reference") or "") or None,
            "selection_kind": str(raw.get("selection_kind") or "none"),
            "target": target,
            "selected_text_length": len(selected_text),
            "selected_text_digest": selected_text_digest,
            "cursor_reference": str(raw.get("cursor_reference") or "") or None,
            "read_only": bool(raw.get("read_only", False)),
            "modified": bool(raw.get("modified", False)),
        }
        fingerprint_json = json.dumps(
            identity,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        context_fingerprint = hashlib.sha256(
            fingerprint_json.encode("utf-8")
        ).hexdigest().upper()
        context = EditContext(
            session_id=session_id,
            app_type=app_type,
            file_path=expected_path,
            document_name=str(
                raw.get("document_name")
                or current.get("document_name")
                or Path(expected_path).name
            )[:260],
            document_fingerprint=expected_document_fingerprint,
            context_fingerprint=context_fingerprint,
            active_container=identity["active_container"],
            selection_reference=identity["selection_reference"],
            selection_kind=identity["selection_kind"],
            target=target,
            selected_text_preview=_preview(selected_text),
            selected_text_length=len(selected_text),
            selected_text_digest=selected_text_digest,
            cursor_reference=identity["cursor_reference"],
            read_only=identity["read_only"],
            modified=identity["modified"],
            captured_at=_timestamp(),
            selected_text_tone=classify_text_tone(selected_text),
        )
        result = context.to_dict()
        result.update({
            "identity_kind": identity_kind,
            "runtime_document_id": identity["runtime_document_id"],
            "window_handle": identity["window_handle"],
            "document_saved": identity["document_saved"],
        })
        return _json_copy(result, "편집 문맥")
