"""Safe native actions for the active Hanword document through HwpObject."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionUnavailable,
    AppActionVerificationError,
    PreparedAction,
)


MAX_DOCUMENT_TEXT_CHARS = 5_000_000
MAX_INSERT_TEXT_CHARS = 5_000
MAX_REPLACE_MATCHES = 10_000
MAX_FORMAT_SELECTION_CHARS = 100_000

PARAGRAPH_ALIGNMENTS = {
    "justify": ("ParagraphShapeAlignJustify", 0),
    "left": ("ParagraphShapeAlignLeft", 1),
    "right": ("ParagraphShapeAlignRight", 2),
    "center": ("ParagraphShapeAlignCenter", 3),
}

COLOR_RGB = {
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128),
}


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


class HwpAdapter:
    supported_operations = frozenset({
        "insert_text",
        "find_replace",
        "set_text_format",
        "set_paragraph_format",
        "save_as",
    })

    def __init__(
        self, object_getter=None, require_visible=True, enable_pdf_export=False
    ):
        self._object_getter = object_getter or _default_hwp_getter
        self._owns_com_initialization = object_getter is None
        self._com_state = threading.local()
        self._require_visible = bool(require_visible)
        self._enable_pdf_export = bool(enable_pdf_export)

    @contextmanager
    def _hwp(self):
        if self._owns_com_initialization and not getattr(
            self._com_state, "initialized", False
        ):
            try:
                import pythoncom

                pythoncom.CoInitialize()
                self._com_state.initialized = True
            except ImportError:
                pass
        try:
            hwp = self._object_getter()
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
            hwp = None

    @staticmethod
    def _created_at():
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _digest_text(value):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _state_fingerprint(snapshot):
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest().upper()

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
            document_name = os.path.basename(full_name) if full_name else "저장되지 않은 문서"
            window_handle = int(window.WindowHandle or 0)
            document_id = (
                os.path.normcase(os.path.abspath(full_name))
                if full_name else f"unsaved:{window_handle}"
            )
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

    @staticmethod
    def _normalize_insert_text(value):
        text = str(value if value is not None else "")
        if not text:
            raise AppActionBlocked("한글에 입력할 텍스트가 비어 있습니다.")
        if len(text) > MAX_INSERT_TEXT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"한 번에 입력할 텍스트는 최대 {MAX_INSERT_TEXT_CHARS:,}자이며 NUL 문자를 포함할 수 없습니다."
            )
        return text.replace("\r\n", "\n").replace("\n", "\r\n")

    def _prepare_insert(self, hwp, params):
        _, base, document_text, selection = self._context(hwp)
        text = self._normalize_insert_text(params.get("text"))
        expected_occurrences = (
            document_text.count(text) - selection["text"].count(text) + 1
        )
        snapshot = {
            **base,
            "operation": "insert_text",
            "target": self._target(base, selection),
            "text_digest_requested": self._digest_text(text),
        }
        return PreparedAction(
            app="hwp",
            operation="insert_text",
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=snapshot["target"],
            params={
                "text": text,
                "expected_occurrences": expected_occurrences,
            },
            current_state={
                "position": base["position"],
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "selected_preview": selection["text"][:120],
                "document_length": len(document_text),
            },
            estimated_changes=max(len(text), selection["text_length"]),
            destructive=selection["has_selection"],
            reversible=True,
            verification_method="read_document_text_and_position",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    @staticmethod
    def _char_state(hwp):
        shape = hwp.HParameterSet.HCharShape
        hwp.HAction.GetDefault("CharShape", shape.HSet)
        return {
            "bold": int(shape.Bold),
            "font_size_hu": int(shape.Height),
            "text_color": int(shape.TextColor),
        }

    @staticmethod
    def _normalize_color_name(value):
        color = str(value or "").strip().casefold()
        aliases = {
            "검은색": "black", "검정": "black",
            "빨간색": "red", "빨강": "red",
            "초록색": "green", "녹색": "green", "초록": "green",
            "파란색": "blue", "파랑": "blue",
            "노란색": "yellow", "노랑": "yellow",
            "주황색": "orange", "주황": "orange",
            "회색": "gray",
        }
        color = aliases.get(color, color)
        if color not in COLOR_RGB:
            raise AppActionBlocked(
                "한글 글자색은 검정·빨강·초록·파랑·노랑·주황·회색을 지원합니다."
            )
        return color

    def _desired_text_format(self, hwp, params):
        supplied = params.get("desired")
        desired = dict(supplied) if isinstance(supplied, dict) else {}
        labels = dict(params.get("format_labels") or {})
        if not isinstance(supplied, dict):
            if params.get("bold") is not None:
                desired["bold"] = 1 if bool(params.get("bold")) else 0
            if params.get("font_size") is not None:
                try:
                    size = float(params.get("font_size"))
                except (TypeError, ValueError) as error:
                    raise AppActionBlocked("한글 글자 크기는 숫자로 지정해주세요.") from error
                if not 1 <= size <= 409:
                    raise AppActionBlocked("한글 글자 크기는 1부터 409포인트 사이여야 합니다.")
                desired["font_size_hu"] = int(hwp.PointToHwpUnit(size))
                labels["font_size"] = int(size) if size.is_integer() else size
            if params.get("text_color") is not None:
                color = self._normalize_color_name(params.get("text_color"))
                desired["text_color"] = int(hwp.RGBColor(*COLOR_RGB[color]))
                labels["text_color"] = color
        allowed = {"bold", "font_size_hu", "text_color"}
        if not desired or set(desired) - allowed:
            raise AppActionBlocked("지원하는 한글 글자 서식을 하나 이상 지정해주세요.")
        desired = {key: int(value) for key, value in desired.items()}
        return desired, labels

    def _prepare_text_format(self, hwp, params):
        _, base, _, selection = self._context(hwp)
        if not selection["has_selection"]:
            raise AppActionBlocked("글자 서식을 적용할 텍스트를 한글에서 먼저 선택해주세요.")
        if selection["text_length"] > MAX_FORMAT_SELECTION_CHARS:
            raise AppActionBlocked(
                f"글자 서식은 한 번에 최대 {MAX_FORMAT_SELECTION_CHARS:,}자까지 지원합니다."
            )
        desired, labels = self._desired_text_format(hwp, params)
        current = self._char_state(hwp)
        noop = all(current.get(key) == value for key, value in desired.items())
        snapshot = {
            **base,
            "operation": "set_text_format",
            "target": "선택 영역",
            "char_state": current,
            "desired": desired,
        }
        return PreparedAction(
            app="hwp",
            operation="set_text_format",
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target="선택 영역",
            params={"desired": desired, "format_labels": labels},
            current_state={
                "selected_length": selection["text_length"],
                "selected_preview": selection["text"][:120],
                "format": current,
            },
            estimated_changes=0 if noop else selection["text_length"],
            destructive=selection["text_length"] > 1000 and not noop,
            reversible=True,
            verification_method="read_selected_char_shape",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    @staticmethod
    def _normalize_alignment(value):
        alignment = str(value or "").strip().casefold()
        aliases = {
            "양쪽": "justify", "양쪽 정렬": "justify", "배분": "justify",
            "왼쪽": "left", "왼쪽 정렬": "left", "좌측": "left",
            "오른쪽": "right", "오른쪽 정렬": "right", "우측": "right",
            "가운데": "center", "가운데 정렬": "center", "중앙": "center",
        }
        alignment = aliases.get(alignment, alignment)
        if alignment not in PARAGRAPH_ALIGNMENTS:
            raise AppActionBlocked("문단 정렬은 왼쪽·가운데·오른쪽·양쪽 정렬을 지원합니다.")
        return alignment

    @staticmethod
    def _paragraph_state(hwp):
        shape = hwp.HParameterSet.HParaShape
        hwp.HAction.GetDefault("ParagraphShape", shape.HSet)
        return {"alignment": int(shape.AlignType)}

    def _prepare_paragraph_format(self, hwp, params):
        _, base, _, selection = self._context(hwp)
        alignment = self._normalize_alignment(params.get("alignment"))
        expected = PARAGRAPH_ALIGNMENTS[alignment][1]
        current = self._paragraph_state(hwp)
        noop = current["alignment"] == expected
        target = self._target(base, selection, paragraph=True)
        snapshot = {
            **base,
            "operation": "set_paragraph_format",
            "target": target,
            "paragraph_state": current,
            "alignment": alignment,
        }
        return PreparedAction(
            app="hwp",
            operation="set_paragraph_format",
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={"alignment": alignment},
            current_state={
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "format": current,
            },
            estimated_changes=0 if noop else max(1, selection["text_length"]),
            destructive=selection["text_length"] > 5000 and not noop,
            reversible=True,
            verification_method="read_paragraph_shape",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    @staticmethod
    def _replace_text(value, find, replace, match_case):
        flags = 0 if match_case else re.IGNORECASE
        return re.sub(re.escape(find), lambda _: replace, value, flags=flags)

    @staticmethod
    def _match_count(value, find, match_case):
        flags = 0 if match_case else re.IGNORECASE
        return len(re.findall(re.escape(find), value, flags=flags))

    def _prepare_find_replace(self, hwp, params):
        _, base, document_text, selection = self._context(hwp)
        scope = str(params.get("scope") or "").strip().casefold()
        aliases = {
            "선택": "selection", "선택 영역": "selection", "selection": "selection",
            "문서": "document", "현재 문서": "document", "document": "document",
        }
        scope = aliases.get(scope, scope)
        if scope not in {"selection", "document"}:
            raise AppActionBlocked("한글 찾기·바꾸기 범위는 선택 영역 또는 현재 문서여야 합니다.")
        if scope == "selection" and not selection["has_selection"]:
            raise AppActionBlocked("찾기·바꾸기에 사용할 텍스트를 한글에서 먼저 선택해주세요.")
        find = str(params.get("find") if params.get("find") is not None else "")
        replace = str(
            params.get("replace") if params.get("replace") is not None else ""
        )
        if not find:
            raise AppActionBlocked("한글에서 찾을 문자열은 비워둘 수 없습니다.")
        if len(find) > 1000 or len(replace) > 1000:
            raise AppActionBlocked("찾을 문자열과 바꿀 문자열은 각각 1,000자 이하여야 합니다.")
        match_case = bool(params.get("match_case"))
        scope_text = selection["text"] if scope == "selection" else document_text
        count = self._match_count(scope_text, find, match_case)
        if count > MAX_REPLACE_MATCHES:
            raise AppActionBlocked(
                f"한 번에 바꿀 수 있는 항목은 최대 {MAX_REPLACE_MATCHES:,}개입니다."
            )
        expected_scope = self._replace_text(scope_text, find, replace, match_case)
        target = "선택 영역" if scope == "selection" else "현재 문서 전체"
        snapshot = {
            **base,
            "operation": "find_replace",
            "target": target,
            "scope": scope,
            "find_digest": self._digest_text(find),
            "replace_digest": self._digest_text(replace),
            "match_case": match_case,
            "matching_count": count,
        }
        noop = count == 0
        return PreparedAction(
            app="hwp",
            operation="find_replace",
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "scope": scope,
                "find": find,
                "replace": replace,
                "match_case": match_case,
                "expected_scope_digest": self._digest_text(expected_scope),
            },
            current_state={
                "matching_count": count,
                "scope_length": len(scope_text),
            },
            estimated_changes=count,
            destructive=not noop,
            reversible=True,
            verification_method="read_hwp_text_after_replace",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    @staticmethod
    def _file_snapshot(path):
        if not os.path.exists(path):
            return {"exists": False}
        if not os.path.isfile(path):
            raise AppActionBlocked("저장 대상 경로가 일반 파일이 아닙니다.")
        stat = os.stat(path)
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "exists": True,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest.hexdigest().upper(),
        }

    def _resolve_pdf_target(self, base, params):
        requested = str(params.get("path") or params.get("target_path") or "").strip()
        if requested:
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(requested)))
        else:
            source = base["full_name"]
            if not source:
                raise AppActionBlocked(
                    "저장되지 않은 한글 문서는 PDF 대상 경로를 직접 지정해주세요."
                )
            path = os.path.splitext(source)[0] + ".pdf"
        if os.path.splitext(path)[1].casefold() != ".pdf":
            raise AppActionBlocked("7단계 다른 이름 저장은 PDF 형식만 지원합니다.")
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise AppActionBlocked("PDF를 저장할 폴더가 존재하지 않습니다.")
        return path

    def _prepare_save_as(self, hwp, params):
        if not self._enable_pdf_export:
            raise AppActionBlocked(
                "현재 한글 2024의 PDF Automation이 간헐적으로 응답을 멈춰 안전상 자동 저장을 차단했습니다. 한글의 PDF 저장 기능을 직접 사용해주세요."
            )
        _, base, _, selection = self._context(hwp)
        requested_format = str(params.get("format") or "PDF").strip().upper()
        if requested_format != "PDF":
            raise AppActionBlocked("7단계 다른 이름 저장은 PDF 형식만 지원합니다.")
        path = self._resolve_pdf_target(base, params)
        target_state = self._file_snapshot(path)
        snapshot = {
            **base,
            "operation": "save_as",
            "target": path,
            "format": "PDF",
            "target_state": target_state,
        }
        return PreparedAction(
            app="hwp",
            operation="save_as",
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=path,
            params={"path": path, "format": "PDF"},
            current_state={
                "target_exists": target_state["exists"],
                "document_modified": base["is_modified"],
                "document_text_digest": base["text_digest"],
                "has_selection": selection["has_selection"],
            },
            estimated_changes=1,
            destructive=bool(target_state["exists"]),
            reversible=False,
            verification_method="verify_pdf_header_and_pages",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def prepare(self, operation, params):
        operation = str(operation or "").strip()
        if operation not in self.supported_operations:
            raise AppActionBlocked(f"아직 지원하지 않는 한글 작업입니다: {operation}")
        if not isinstance(params, dict):
            raise AppActionBlocked("한글 작업의 params는 객체 형식이어야 합니다.")
        with self._hwp() as hwp:
            if operation == "insert_text":
                return self._prepare_insert(hwp, params)
            if operation == "set_text_format":
                return self._prepare_text_format(hwp, params)
            if operation == "set_paragraph_format":
                return self._prepare_paragraph_format(hwp, params)
            if operation == "find_replace":
                return self._prepare_find_replace(hwp, params)
            return self._prepare_save_as(hwp, params)

    def execute(self, prepared):
        if prepared.app != "hwp" or prepared.operation not in self.supported_operations:
            raise AppActionBlocked("지원되는 한글 PreparedAction만 실행할 수 있습니다.")
        with self._hwp() as hwp:
            if prepared.operation == "insert_text":
                return self._execute_insert(hwp, prepared)
            if prepared.operation == "set_text_format":
                return self._execute_text_format(hwp, prepared)
            if prepared.operation == "set_paragraph_format":
                return self._execute_paragraph_format(hwp, prepared)
            if prepared.operation == "find_replace":
                return self._execute_find_replace(hwp, prepared)
            return self._execute_save_as(hwp, prepared)

    @staticmethod
    def _undo(hwp):
        hwp.HAction.Run("Undo")

    def _execute_insert(self, hwp, prepared):
        current = self._prepare_insert(hwp, prepared.params)
        self._ensure_same_context(current, prepared)
        _, before_base, before_text, _ = self._context(hwp)
        try:
            action = hwp.CreateAction("InsertText")
            parameter_set = action.CreateSet()
            parameter_set.SetItem("Text", current.params["text"])
            action.Execute(parameter_set)
            _, after_base, after_text, _ = self._context(hwp)
            if (
                after_base["text_digest"] == before_base["text_digest"]
                or after_text.count(current.params["text"])
                != current.params["expected_occurrences"]
            ):
                raise AppActionVerificationError("한글 텍스트 입력 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                self._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 텍스트 입력 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            current,
            {"position": before_base["position"]},
            {"position": after_base["position"], "inserted_length": len(current.params["text"])},
            True,
        )

    def _execute_text_format(self, hwp, prepared):
        current = self._prepare_text_format(hwp, prepared.params)
        self._ensure_same_context(current, prepared)
        before = current.current_state["format"]
        if current.noop:
            return self._result(current, before, before, False)
        try:
            shape = hwp.HParameterSet.HCharShape
            hwp.HAction.GetDefault("CharShape", shape.HSet)
            for key, attribute in (
                ("bold", "Bold"),
                ("font_size_hu", "Height"),
                ("text_color", "TextColor"),
            ):
                if key in current.params["desired"]:
                    setattr(shape, attribute, current.params["desired"][key])
            hwp.HAction.Execute("CharShape", shape.HSet)
            after = self._char_state(hwp)
            if not all(
                after.get(key) == value
                for key, value in current.params["desired"].items()
            ):
                raise AppActionVerificationError("한글 선택 영역의 글자 서식이 요청과 다릅니다.")
        except Exception as error:
            try:
                self._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, True)

    def _execute_paragraph_format(self, hwp, prepared):
        current = self._prepare_paragraph_format(hwp, prepared.params)
        self._ensure_same_context(current, prepared)
        before = current.current_state["format"]
        if current.noop:
            return self._result(current, before, before, False)
        action_name, expected = PARAGRAPH_ALIGNMENTS[current.params["alignment"]]
        try:
            hwp.HAction.Run(action_name)
            after = self._paragraph_state(hwp)
            if after["alignment"] != expected:
                raise AppActionVerificationError("한글 문단 정렬 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                self._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 문단 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, True)

    def _execute_find_replace(self, hwp, prepared):
        current = self._prepare_find_replace(hwp, prepared.params)
        self._ensure_same_context(current, prepared)
        if current.noop:
            before = {"matching_count": 0}
            return self._result(current, before, before, False)
        _, before_base, before_text, selection = self._context(hwp)
        scope_text = (
            selection["text"] if current.params["scope"] == "selection" else before_text
        )
        expected = self._replace_text(
            scope_text,
            current.params["find"],
            current.params["replace"],
            current.params["match_case"],
        )
        try:
            find_replace = hwp.HParameterSet.HFindReplace
            hwp.HAction.GetDefault("AllReplace", find_replace.HSet)
            find_replace.Direction = hwp.FindDir("AllDoc")
            find_replace.FindString = current.params["find"]
            find_replace.ReplaceString = current.params["replace"]
            find_replace.ReplaceMode = 1
            find_replace.FindType = 1
            find_replace.MatchCase = 1 if current.params["match_case"] else 0
            is_selection = current.params["scope"] == "selection"
            find_replace.IgnoreMessage = 0 if is_selection else 1
            if is_selection:
                hwp.SetMessageBoxMode(0x20000)
            try:
                hwp.HAction.Execute("AllReplace", find_replace.HSet)
            finally:
                if is_selection:
                    hwp.SetMessageBoxMode(0xF0000)
            _, after_base, after_text, after_selection = self._context(hwp)
            actual = (
                after_selection["text"] if is_selection else after_text
            )
            if self._digest_text(actual) != self._digest_text(expected):
                raise AppActionVerificationError("한글 찾기·바꾸기 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                hwp.SetMessageBoxMode(0xF0000)
            except Exception:
                pass
            try:
                self._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 찾기·바꾸기 실행 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            current,
            {"matching_count": current.current_state["matching_count"], "text_digest": before_base["text_digest"]},
            {"replaced_count": current.current_state["matching_count"], "text_digest": after_base["text_digest"]},
            True,
        )

    @staticmethod
    def _verify_pdf(path):
        if not os.path.isfile(path) or os.path.getsize(path) < 100:
            raise AppActionVerificationError("생성된 PDF 파일이 없거나 비어 있습니다.")
        with open(path, "rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise AppActionVerificationError("생성된 파일이 올바른 PDF 형식이 아닙니다.")
        try:
            from pypdf import PdfReader

            page_count = len(PdfReader(path).pages)
        except Exception as error:
            raise AppActionVerificationError("생성된 PDF를 다시 열어 검증하지 못했습니다.") from error
        if page_count < 1:
            raise AppActionVerificationError("생성된 PDF에 페이지가 없습니다.")
        return {"size": os.path.getsize(path), "page_count": page_count}

    def _execute_save_as(self, hwp, prepared):
        current = self._prepare_save_as(hwp, prepared.params)
        self._ensure_same_context(current, prepared)
        target = current.params["path"]
        parent = os.path.dirname(target)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".jarvis-hwp-", suffix=".pdf", dir=parent
        )
        os.close(descriptor)
        os.unlink(temp_path)
        backup_path = None
        committed = False
        try:
            action = hwp.CreateAction("PrintToPDFEx")
            parameter_set = action.CreateSet()
            action.GetDefault(parameter_set)
            parameter_set.SetItem("FileName", temp_path)
            parameter_set.SetItem("Range", 6)
            action.Execute(parameter_set)
            pdf_state = self._verify_pdf(temp_path)
            if os.path.exists(target):
                descriptor, backup_path = tempfile.mkstemp(
                    prefix=".jarvis-hwp-backup-", suffix=".pdf", dir=parent
                )
                os.close(descriptor)
                shutil.copy2(target, backup_path)
            os.replace(temp_path, target)
            committed = True
            final_state = self._verify_pdf(target)
            _, after_base, _, _ = self._context(hwp)
            if (
                after_base["document_id"] != current.document_id
                or after_base["text_digest"]
                != current.current_state["document_text_digest"]
            ):
                raise AppActionVerificationError(
                    "PDF 저장 중 활성 한글 문서 또는 문서 내용이 바뀌었습니다."
                )
        except Exception as error:
            if committed:
                try:
                    if backup_path and os.path.exists(backup_path):
                        os.replace(backup_path, target)
                        backup_path = None
                    elif os.path.exists(target):
                        os.unlink(target)
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 PDF 저장 또는 검증에 실패했습니다."
            ) from error
        finally:
            for path in (temp_path, backup_path):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        return self._result(
            current,
            {"target_exists": current.current_state["target_exists"]},
            {"path": target, **final_state, "temporary_pdf": pdf_state},
            True,
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
