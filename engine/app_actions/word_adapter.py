"""Safe native actions for the active connected Microsoft Word document."""

from __future__ import annotations

import hashlib
import os

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


MAX_WORD_TEXT_CHARS = 5_000_000
MAX_WORD_SELECTION_CHARS = 50_000
MAX_WORD_REPLACEMENT_CHARS = 10_000
WD_NO_PROTECTION = -1
WD_WITHIN_TABLE = 12
WD_ALIGNMENTS = {
    "left": 0,
    "center": 1,
    "right": 2,
    "justify": 3,
}


class WordAdapter:
    supported_operations = frozenset(
        {
            "replace_selection",
            "set_text_format",
            "set_paragraph_format",
            "save_document",
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
    def _digest(value) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _style_name(selection) -> str:
        try:
            style = selection.Style
            return str(getattr(style, "NameLocal", style) or "")
        except Exception:
            return ""

    @staticmethod
    def _uniform_int(value, label):
        try:
            number = int(value)
        except (TypeError, ValueError) as error:
            raise AppActionBlocked(f"Word {label} 상태를 읽지 못했습니다.") from error
        if abs(number) >= 9_999_999:
            raise AppActionBlocked(
                f"선택 영역의 Word {label}이 서로 달라 한 번에 변경하지 않습니다."
            )
        return number

    @staticmethod
    def _uniform_float(value, label):
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise AppActionBlocked(f"Word {label} 상태를 읽지 못했습니다.") from error
        if abs(number) >= 9_999_999:
            raise AppActionBlocked(
                f"선택 영역의 Word {label}이 서로 달라 한 번에 변경하지 않습니다."
            )
        return number

    def _context(self, application, document):
        if bool(getattr(document, "ReadOnly", False)):
            raise AppActionBlocked("현재 Word 문서는 읽기 전용이라 변경하지 않습니다.")
        protection = int(getattr(document, "ProtectionType", WD_NO_PROTECTION))
        if protection != WD_NO_PROTECTION:
            raise AppActionBlocked("현재 Word 문서가 보호되어 있어 변경하지 않습니다.")
        selection = application.Selection
        selection_document = getattr(selection, "Document", None)
        if selection_document is not None and office_document_id(
            selection_document
        ) != office_document_id(document):
            raise AppActionBlocked("Word 선택 영역이 연결 문서에 속하지 않습니다.")
        start = int(selection.Start)
        end = int(selection.End)
        text = str(selection.Text or "")
        document_text = str(document.Content.Text or "")
        if len(document_text) > MAX_WORD_TEXT_CHARS:
            raise AppActionBlocked(
                f"Word 문서 텍스트가 {MAX_WORD_TEXT_CHARS:,}자를 넘어 편집 범위를 벗어납니다."
            )
        if len(text) > MAX_WORD_SELECTION_CHARS:
            raise AppActionBlocked(
                f"Word 선택 텍스트는 한 번에 {MAX_WORD_SELECTION_CHARS:,}자까지 지원합니다."
            )
        in_table = False
        table = {}
        try:
            in_table = bool(selection.Information(WD_WITHIN_TABLE))
            if in_table:
                cell = selection.Cells.Item(1)
                table = {
                    "row": int(cell.RowIndex),
                    "column": int(cell.ColumnIndex),
                    "range_start": int(cell.Range.Start),
                    "range_end": int(cell.Range.End),
                }
        except Exception:
            in_table = False
            table = {}
        font = selection.Font
        paragraph = selection.ParagraphFormat
        state = {
            "document_id": office_document_id(document),
            "document_name": str(document.Name),
            "start": start,
            "end": end,
            "has_selection": start != end,
            "selected_text": text,
            "selected_length": len(text),
            "selected_digest": self._digest(text),
            "document_length": len(document_text),
            "document_digest": self._digest(document_text),
            "style_name": self._style_name(selection),
            "bold": int(font.Bold),
            "font_size": float(font.Size),
            "alignment": int(paragraph.Alignment),
            "in_table": in_table,
            "table": table,
            "saved": bool(document.Saved),
        }
        return selection, state

    @staticmethod
    def _target(state):
        if state["in_table"]:
            table = state["table"]
            return f"표 R{table['row']}C{table['column']} · {state['start']}:{state['end']}"
        return f"Word Range {state['start']}:{state['end']}"

    @staticmethod
    def _base_snapshot(state, operation):
        return {
            "document_id": state["document_id"],
            "operation": operation,
            "start": state["start"],
            "end": state["end"],
            "selected_digest": state["selected_digest"],
            "document_digest": state["document_digest"],
            "saved": state["saved"],
        }

    @staticmethod
    def _document_path(params):
        path = str(params.get("document_path") or "").strip()
        if not path or not os.path.isfile(path):
            raise AppActionBlocked("연결된 Word 문서 경로를 확인하지 못했습니다.")
        return path

    def _prepare_replace(self, application, document, params):
        _, state = self._context(application, document)
        if not state["has_selection"]:
            raise AppActionBlocked("교체할 Word 텍스트를 먼저 선택해주세요.")
        text = str(params.get("text") if params.get("text") is not None else "")
        if not text or len(text) > MAX_WORD_REPLACEMENT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"Word 교체 텍스트는 1~{MAX_WORD_REPLACEMENT_CHARS:,}자여야 합니다."
            )
        noop = text == state["selected_text"]
        snapshot = {
            **self._base_snapshot(state, "replace_selection"),
            "requested_digest": self._digest(text),
        }
        return PreparedAction(
            app="word",
            operation="replace_selection",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "text": text,
                "original_text": state["selected_text"],
                "start": state["start"],
                "end": state["end"],
            },
            current_state={
                "has_selection": True,
                "selected_length": state["selected_length"],
                "selected_digest": state["selected_digest"],
                "selected_preview": state["selected_text"][:120],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            },
            estimated_changes=0 if noop else max(len(text), state["selected_length"]),
            destructive=not noop,
            reversible=True,
            verification_method="read_word_range_text",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    @staticmethod
    def _normalize_alignment(value):
        numeric = {
            0: ("left", 0),
            1: ("center", 1),
            2: ("right", 2),
            3: ("justify", 3),
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
        if text not in WD_ALIGNMENTS:
            raise AppActionBlocked("Word 정렬은 왼쪽·가운데·오른쪽·양쪽을 지원합니다.")
        return text, WD_ALIGNMENTS[text]

    def _desired_text_format(self, params, state):
        prepared_desired = params.get("desired")
        if isinstance(prepared_desired, dict):
            desired = {}
            if prepared_desired.get("bold") is not None:
                self._uniform_int(state["bold"], "굵기")
                desired["bold"] = -1 if int(prepared_desired["bold"]) != 0 else 0
            if prepared_desired.get("font_size") is not None:
                self._uniform_float(state["font_size"], "글자 크기")
                size = float(prepared_desired["font_size"])
                if not 1 <= size <= 1638:
                    raise AppActionBlocked("Word 글자 크기는 1~1638pt 사이여야 합니다.")
                desired["font_size"] = size
            if desired:
                return desired
        desired = {}
        if params.get("bold") is not None:
            self._uniform_int(state["bold"], "굵기")
            desired["bold"] = -1 if bool(params["bold"]) else 0
        if params.get("font_size") is not None:
            self._uniform_float(state["font_size"], "글자 크기")
            size = float(params["font_size"])
            if not 1 <= size <= 1638:
                raise AppActionBlocked("Word 글자 크기는 1~1638pt 사이여야 합니다.")
            desired["font_size"] = size
        elif params.get("font_size_delta") is not None:
            self._uniform_float(state["font_size"], "글자 크기")
            size = state["font_size"] + float(params["font_size_delta"])
            if not 1 <= size <= 1638:
                raise AppActionBlocked("변경할 Word 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        if not desired:
            raise AppActionBlocked("변경할 Word 글자 서식을 지정해주세요.")
        return desired

    def _prepare_text_format(self, application, document, params):
        _, state = self._context(application, document)
        if not state["has_selection"]:
            raise AppActionBlocked("서식을 적용할 Word 텍스트를 먼저 선택해주세요.")
        desired = self._desired_text_format(params, state)
        noop = all(state[key] == value for key, value in desired.items())
        snapshot = {
            **self._base_snapshot(state, "set_text_format"),
            "current": {"bold": state["bold"], "font_size": state["font_size"]},
            "desired": desired,
        }
        return PreparedAction(
            app="word",
            operation="set_text_format",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "start": state["start"],
                "end": state["end"],
                "desired": desired,
                "original": {
                    "bold": state["bold"],
                    "font_size": state["font_size"],
                },
            },
            current_state={
                "has_selection": True,
                "selected_length": state["selected_length"],
                "selected_digest": state["selected_digest"],
                "selected_preview": state["selected_text"][:120],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            },
            estimated_changes=0 if noop else state["selected_length"],
            destructive=not noop and state["selected_length"] > 1000,
            reversible=True,
            verification_method="read_word_font",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def _prepare_paragraph_format(self, application, document, params):
        _, state = self._context(application, document)
        self._uniform_int(state["alignment"], "문단 정렬")
        label, alignment = self._normalize_alignment(params.get("alignment"))
        noop = state["alignment"] == alignment
        snapshot = {
            **self._base_snapshot(state, "set_paragraph_format"),
            "current_alignment": state["alignment"],
            "desired_alignment": alignment,
        }
        return PreparedAction(
            app="word",
            operation="set_paragraph_format",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=self._target(state),
            params={
                "document_path": state["document_id"],
                "start": state["start"],
                "end": state["end"],
                "alignment": alignment,
                "alignment_label": label,
                "original_alignment": state["alignment"],
            },
            current_state={
                "has_selection": state["has_selection"],
                "selected_length": state["selected_length"],
                "selected_digest": state["selected_digest"],
                "selected_preview": state["selected_text"][:120],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            },
            estimated_changes=0 if noop else max(1, state["selected_length"]),
            destructive=False,
            reversible=True,
            verification_method="read_word_paragraph_alignment",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def _prepare_save(self, application, document, params):
        _, state = self._context(application, document)
        stat = os.stat(state["document_id"])
        snapshot = {
            **self._base_snapshot(state, "save_document"),
            "file_size": int(stat.st_size),
            "file_mtime_ns": int(stat.st_mtime_ns),
        }
        return PreparedAction(
            app="word",
            operation="save_document",
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=state["document_name"],
            params={"document_path": state["document_id"]},
            current_state={
                "saved": state["saved"],
                "document_length": state["document_length"],
            },
            estimated_changes=0 if state["saved"] else 1,
            destructive=not state["saved"],
            reversible=False,
            verification_method="verify_word_saved_and_file_exists",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=state["saved"],
        )

    def prepare(self, operation, params):
        operation = str(operation or "").strip()
        if operation not in self.supported_operations:
            raise AppActionBlocked(f"아직 지원하지 않는 Word 작업입니다: {operation}")
        if not isinstance(params, dict):
            raise AppActionBlocked("Word 작업의 params는 객체 형식이어야 합니다.")
        path = self._document_path(params)
        with exact_office_document(
            "word",
            path,
            application_getter=self._application_getter,
            require_visible=self._require_visible,
            com_runtime=self._com_runtime,
        ) as (application, document):
            return self._prepare_in_document(
                application,
                document,
                operation,
                params,
            )

    def read_selection(self, document_path):
        """Read the exact current Word selection for a bounded local transform."""
        path = self._document_path({"document_path": document_path})
        with exact_office_document(
            "word",
            path,
            application_getter=self._application_getter,
            require_visible=self._require_visible,
            com_runtime=self._com_runtime,
        ) as (application, document):
            _, state = self._context(application, document)
            return {
                "document_id": state["document_id"],
                "start": state["start"],
                "end": state["end"],
                "has_selection": state["has_selection"],
                "text": state["selected_text"],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            }

    def _prepare_in_document(self, application, document, operation, params):
        if operation == "replace_selection":
            return self._prepare_replace(application, document, params)
        if operation == "set_text_format":
            return self._prepare_text_format(application, document, params)
        if operation == "set_paragraph_format":
            return self._prepare_paragraph_format(application, document, params)
        return self._prepare_save(application, document, params)

    @staticmethod
    def _ensure_same_context(current, prepared):
        if current.context_fingerprint != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인 이후 Word 문서·선택 Range·내용 또는 서식이 바뀌어 실행하지 않았습니다."
            )

    @staticmethod
    def _result(prepared, changed, observations):
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "word",
            "operation": prepared.operation,
            "document_id": prepared.document_id,
            "target": prepared.target,
            "changed": bool(changed),
            **dict(observations),
        }

    def execute(self, prepared):
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        if prepared.app != "word" or prepared.operation not in self.supported_operations:
            raise AppActionBlocked("지원되는 Word PreparedAction만 실행할 수 있습니다.")
        path = prepared.params.get("document_path")
        with exact_office_document(
            "word",
            path,
            application_getter=self._application_getter,
            require_visible=self._require_visible,
            com_runtime=self._com_runtime,
        ) as (application, document):
            current = self._prepare_in_document(
                application,
                document,
                prepared.operation,
                prepared.params,
            )
            self._ensure_same_context(current, prepared)
            if current.noop:
                return self._result(current, False, {"noop": True})
            if prepared.operation == "replace_selection":
                return self._execute_replace(document, current)
            if prepared.operation == "set_text_format":
                return self._execute_text_format(document, current)
            if prepared.operation == "set_paragraph_format":
                return self._execute_paragraph_format(document, current)
            return self._execute_save(document, current)

    def _execute_replace(self, document, prepared):
        start = int(prepared.params["start"])
        end = int(prepared.params["end"])
        text = prepared.params["text"]
        original = prepared.params["original_text"]
        target = document.Range(start, end)
        try:
            target.Text = text
            actual = str(document.Range(start, start + len(text)).Text or "")
            if actual != text:
                raise AppActionVerificationError(
                    "Word 선택 텍스트 교체 결과가 요청과 다릅니다."
                )
            verified_range = document.Range(start, start + len(text))
            verified_range.Select()
            post_format = {
                "bold": int(verified_range.Font.Bold),
                "font_size": float(verified_range.Font.Size),
                "alignment": int(
                    verified_range.ParagraphFormat.Alignment
                ),
            }
        except Exception as error:
            try:
                document.Range(start, start + len(text)).Text = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 선택 텍스트 교체 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            prepared,
            True,
            {
                "text_length": len(text),
                "document_digest": self._digest(document.Content.Text),
                "format": post_format,
            },
        )

    @staticmethod
    def _apply_text_format(target, desired):
        if "bold" in desired:
            target.Font.Bold = desired["bold"]
        if "font_size" in desired:
            target.Font.Size = desired["font_size"]

    def _execute_text_format(self, document, prepared):
        target = document.Range(prepared.params["start"], prepared.params["end"])
        desired = prepared.params["desired"]
        original = prepared.params["original"]
        try:
            self._apply_text_format(target, desired)
            if "bold" in desired and int(target.Font.Bold) != int(desired["bold"]):
                raise AppActionVerificationError("Word 굵기 적용 결과가 다릅니다.")
            if "font_size" in desired and abs(
                float(target.Font.Size) - float(desired["font_size"])
            ) > 0.01:
                raise AppActionVerificationError("Word 글자 크기 적용 결과가 다릅니다.")
        except Exception as error:
            try:
                self._apply_text_format(target, original)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"format": desired})

    def _execute_paragraph_format(self, document, prepared):
        target = document.Range(prepared.params["start"], prepared.params["end"])
        alignment = int(prepared.params["alignment"])
        original = int(prepared.params["original_alignment"])
        try:
            target.ParagraphFormat.Alignment = alignment
            if int(target.ParagraphFormat.Alignment) != alignment:
                raise AppActionVerificationError("Word 문단 정렬 결과가 다릅니다.")
        except Exception as error:
            try:
                target.ParagraphFormat.Alignment = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 문단 정렬 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, True, {"alignment": alignment})

    def _execute_save(self, document, prepared):
        try:
            document.Save()
            if not bool(document.Saved) or not os.path.isfile(prepared.document_id):
                raise AppActionVerificationError("Word 문서 저장 결과를 확인하지 못했습니다.")
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionVerificationError("Word 문서 저장에 실패했습니다.") from error
        return self._result(prepared, True, {"saved": True})

    def undo(self, prepared, record=None):
        """Restore one verified Word text or formatting snapshot."""
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        if prepared.app != "word" or not prepared.reversible:
            raise AppActionBlocked("되돌릴 수 있는 Word 작업이 아닙니다.")
        if prepared.operation not in {
            "replace_selection",
            "set_text_format",
            "set_paragraph_format",
        }:
            raise AppActionBlocked(f"직전 Word {prepared.operation} 작업은 복원할 수 없습니다.")
        observations = dict((record or {}).get("after_observations") or {})
        path = prepared.params.get("document_path")
        with exact_office_document(
            "word",
            path,
            application_getter=self._application_getter,
            require_visible=self._require_visible,
            com_runtime=self._com_runtime,
        ) as (_, document):
            start = int(prepared.params["start"])
            end = int(prepared.params["end"])
            if prepared.operation == "replace_selection":
                text = str(prepared.params["text"])
                original = str(prepared.params["original_text"])
                expected_digest = str(observations.get("document_digest") or "")
                if expected_digest and self._digest(document.Content.Text) != expected_digest:
                    raise AppActionContextChanged(
                        "Word 문서 내용이 직전 편집 후 바뀌어 복원하지 않았습니다."
                    )
                target = document.Range(start, start + len(text))
                if str(target.Text or "") != text:
                    raise AppActionContextChanged(
                        "Word 직전 교체 텍스트가 달라져 복원하지 않았습니다."
                    )
                target.Text = original
                restored = document.Range(start, start + len(original))
                if str(restored.Text or "") != original:
                    raise AppActionVerificationError("Word 원본 텍스트 복원을 확인하지 못했습니다.")
                restored.Select()
            elif prepared.operation == "set_text_format":
                target = document.Range(start, end)
                desired = dict(prepared.params["desired"])
                if "bold" in desired and int(target.Font.Bold) != int(desired["bold"]):
                    raise AppActionContextChanged("Word 굵기가 직전 편집 결과와 다릅니다.")
                if "font_size" in desired and abs(
                    float(target.Font.Size) - float(desired["font_size"])
                ) > 0.01:
                    raise AppActionContextChanged(
                        "Word 글자 크기가 직전 편집 결과와 다릅니다."
                    )
                original = dict(prepared.params["original"])
                self._apply_text_format(target, original)
                if int(target.Font.Bold) != int(original["bold"]) or abs(
                    float(target.Font.Size) - float(original["font_size"])
                ) > 0.01:
                    raise AppActionVerificationError("Word 글자 서식 복원을 확인하지 못했습니다.")
                target.Select()
            else:
                target = document.Range(start, end)
                desired = int(prepared.params["alignment"])
                if int(target.ParagraphFormat.Alignment) != desired:
                    raise AppActionContextChanged(
                        "Word 문단 정렬이 직전 편집 결과와 다릅니다."
                    )
                original = int(prepared.params["original_alignment"])
                target.ParagraphFormat.Alignment = original
                if int(target.ParagraphFormat.Alignment) != original:
                    raise AppActionVerificationError("Word 문단 정렬 복원을 확인하지 못했습니다.")
                target.Select()
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": "undo_last_edit",
            "restored_operation": prepared.operation,
            "document_id": prepared.document_id,
            "target": prepared.target,
        }
