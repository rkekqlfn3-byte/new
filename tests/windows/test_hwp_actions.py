import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pypdf import PdfWriter

from engine.api import command_api
from engine.app_actions.base import AppActionBlocked, AppActionContextChanged
from engine.app_actions.hwp_adapter import HwpAdapter
from engine.app_actions.operations.hwp.table_cell import CELL_SEPARATOR
from engine.app_actions.registry import AppActionRegistry
from engine.decision import DecisionEngine, PreferenceManager
from engine.execution_runtime import ExecutionController
from engine.parser import CommandParser


class FakeSet:
    def __init__(self):
        self.HSet = self
        self.items = {}

    def SetItem(self, key, value):
        self.items[key] = value
        setattr(self, key, value)


class FakeShapeSet(FakeSet):
    def __init__(self, **values):
        super().__init__()
        for key, value in values.items():
            setattr(self, key, value)


class FakeDocument:
    def __init__(self, full_name="", edit_mode=1):
        self.FullName = full_name
        self.Path = os.path.dirname(full_name) + os.sep if full_name else ""
        self.EditMode = edit_mode


class FakeDocuments:
    Count = 1

    def __init__(self, document):
        self.Active_XHwpDocument = document


class FakeWindow:
    Visible = True
    WindowHandle = 778899


class FakeWindows:
    Count = 1

    def __init__(self):
        self.Active_XHwpWindow = FakeWindow()


class FakeAction:
    def __init__(self, hwp, name):
        self.hwp = hwp
        self.name = name

    def CreateSet(self):
        return FakeSet()

    def GetDefault(self, _parameter_set):
        return True

    def Execute(self, parameter_set):
        if self.name == "InsertText":
            self.hwp._push_undo()
            text = parameter_set.items.get("Text", "")
            if self.hwp.cell is not None:
                self.hwp.cells[self.hwp.cell] = text
                self.hwp.IsModified = True
                return True
            if self.hwp.selection is not None:
                start, end = self.hwp.selection
                self.hwp.text = self.hwp.text[:start] + text + self.hwp.text[end:]
                self.hwp.cursor = start + len(text)
                self.hwp.selection = None
            else:
                pos = self.hwp.cursor
                self.hwp.text = self.hwp.text[:pos] + text + self.hwp.text[pos:]
                self.hwp.cursor += len(text)
            self.hwp.IsModified = True
            return True
        if self.name == "PrintToPDFEx":
            path = parameter_set.items["FileName"]
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with open(path, "wb") as stream:
                writer.write(stream)
            return True
        return False


class FakeControl:
    """One anchored 한글 object in the HeadCtrl/Next chain."""

    def __init__(self, ctrl_id, index=0):
        self.CtrlID = ctrl_id
        self.Next = None
        self.index = index

    def GetAnchorPos(self, _mode):
        return ("anchor", self.index)


class FakeHAction:
    def __init__(self, hwp):
        self.hwp = hwp

    def GetDefault(self, name, parameter_set):
        if name == "CharShape":
            parameter_set.Bold = self.hwp.char_state["Bold"]
            parameter_set.Height = self.hwp.char_state["Height"]
            parameter_set.TextColor = self.hwp.char_state["TextColor"]
        elif name == "ParagraphShape":
            parameter_set.AlignType = self.hwp.paragraph_alignment
            parameter_set.LineSpacing = self.hwp.line_spacing
            parameter_set.LineSpacingType = self.hwp.line_spacing_type
            parameter_set.HeadingType = self.hwp.heading_type
        return True

    def Execute(self, name, parameter_set):
        if name == "TableCreate":
            self.hwp._push_undo()
            self.hwp.tables.append(
                (int(parameter_set.Rows), int(parameter_set.Cols))
            )
            self.hwp._rebuild_controls()
            self.hwp.IsModified = True
            return True
        if name == "ParagraphShape":
            self.hwp._push_undo()
            self.hwp.paragraph_alignment = int(parameter_set.AlignType)
            self.hwp.line_spacing = int(parameter_set.LineSpacing)
            self.hwp.line_spacing_type = int(parameter_set.LineSpacingType)
            self.hwp.heading_type = int(parameter_set.HeadingType)
            return True
        if name == "CharShape":
            self.hwp._push_undo()
            self.hwp.char_state = {
                "Bold": int(parameter_set.Bold),
                "Height": int(parameter_set.Height),
                "TextColor": int(parameter_set.TextColor),
            }
            return True
        if name == "AllReplace":
            self.hwp._push_undo()
            flags = 0 if int(parameter_set.MatchCase) else re.IGNORECASE
            pattern = re.compile(re.escape(parameter_set.FindString), flags)
            selected_only = (
                int(parameter_set.IgnoreMessage) == 0
                and self.hwp.message_mode == 0x20000
                and self.hwp.selection is not None
            )
            if selected_only:
                start, end = self.hwp.selection
                value = pattern.sub(parameter_set.ReplaceString, self.hwp.text[start:end])
                self.hwp.text = self.hwp.text[:start] + value + self.hwp.text[end:]
                self.hwp.selection = (start, start + len(value))
            else:
                self.hwp.text = pattern.sub(parameter_set.ReplaceString, self.hwp.text)
            self.hwp.IsModified = True
            return True
        return False

    def Run(self, name):
        if name == "ShapeObjTableSelCell":
            self.hwp.cell = (1, 1)
            return True
        if name in {"TableRightCell", "TableLowerCell"}:
            return self.hwp._step_cell(name)
        if name == "Undo":
            self.hwp._undo()
            return True
        alignments = {
            "ParagraphShapeAlignJustify": 0,
            "ParagraphShapeAlignLeft": 1,
            "ParagraphShapeAlignRight": 2,
            "ParagraphShapeAlignCenter": 3,
        }
        if name in alignments:
            self.hwp._push_undo()
            self.hwp.paragraph_alignment = alignments[name]
            return False
        if name == "Delete":
            if self.hwp.selection is None:
                return False
            self.hwp._push_undo()
            start, end = self.hwp.selection
            self.hwp.text = self.hwp.text[:start] + self.hwp.text[end:]
            self.hwp.cursor = start
            self.hwp.selection = None
            self.hwp.IsModified = True
            return True
        if name == "BreakPage":
            if self.hwp.cell is not None:
                # 한글 refuses a page break inside a table and returns False.
                return False
            self.hwp._push_undo()
            self.hwp.pages += 1
            self.hwp.text += ""
            self.hwp.IsModified = True
            return True
        if name == "SelectAll":
            self.hwp.selection = (0, len(self.hwp.text))
            return True
        if name == "Cancel":
            self.hwp.selection = None
            self.hwp.cell = None
            return True
        return False


class FakeHwp:
    def __init__(self, text="", full_name="", edit_mode=1):
        self.text = text
        self.cursor = len(text)
        self.selection = None
        self.IsModified = bool(text)
        self.document = FakeDocument(full_name, edit_mode)
        self.XHwpDocuments = FakeDocuments(self.document)
        self.XHwpWindows = FakeWindows()
        self.char_state = {"Bold": 0, "Height": 1000, "TextColor": 0}
        self.paragraph_alignment = 0
        self.line_spacing = 160
        self.line_spacing_type = 0
        self.heading_type = 0
        self.pages = 1
        self.message_mode = 0xF0000
        self.undo_stack = []
        self.HParameterSet = SimpleNamespace(
            HCharShape=FakeShapeSet(Bold=0, Height=1000, TextColor=0),
            HParaShape=FakeShapeSet(
                AlignType=0, LineSpacing=160, LineSpacingType=0, HeadingType=0
            ),
            HTableCreation=FakeShapeSet(
                Rows=1, Cols=1, WidthType=0, HeightType=0
            ),
            HFindReplace=FakeShapeSet(
                Direction=2,
                FindString="",
                ReplaceString="",
                ReplaceMode=1,
                IgnoreMessage=1,
                FindType=1,
                MatchCase=0,
            ),
        )
        self.tables = []
        self.cells = {}
        self.cell = None
        self.HeadCtrl = None
        self.HAction = FakeHAction(self)

    def SetPosBySet(self, position):
        self.table_index = int(position[1])
        return True

    def FindCtrl(self):
        return True

    def _step_cell(self, action):
        if self.cell is None or not self.tables:
            return False
        rows, columns = self.tables[0]
        row, column = self.cell
        if action == "TableRightCell":
            if column >= columns:
                return False
            self.cell = (row, column + 1)
        else:
            if row >= rows:
                return False
            self.cell = (row + 1, column)
        return True

    def _rebuild_controls(self):
        head = None
        previous = None
        for index, _ in enumerate(self.tables):
            control = FakeControl("tbl", index)
            if previous is None:
                head = control
            else:
                previous.Next = control
            previous = control
        self.HeadCtrl = head

    def _push_undo(self):
        self.undo_stack.append((
            self.text,
            self.cursor,
            self.selection,
            dict(self.char_state),
            self.paragraph_alignment,
            self.line_spacing,
            self.line_spacing_type,
            self.heading_type,
            self.pages,
            list(self.tables),
            dict(self.cells),
            self.IsModified,
        ))

    def _undo(self):
        if self.undo_stack:
            (
                self.text,
                self.cursor,
                self.selection,
                self.char_state,
                self.paragraph_alignment,
                self.line_spacing,
                self.line_spacing_type,
                self.heading_type,
                self.pages,
                self.tables,
                self.cells,
                self.IsModified,
            ) = self.undo_stack.pop()
            self._rebuild_controls()

    def select(self, start=0, end=None):
        self.selection = (start, len(self.text) if end is None else end)

    def GetSelectedPos(self):
        if self.selection is None:
            return (False, 0, 0, 0, 0, 0, 0)
        start, end = self.selection
        return (True, 0, 0, 16 + start, 0, 0, 16 + end)

    def GetTextFile(self, _format, option):
        if option == "saveblock" and self.cell is not None:
            rows, columns = self.tables[0]
            slots = [""]
            for row in range(1, rows + 1):
                for column in range(1, columns + 1):
                    slots.append(self.cells.get((row, column), ""))
            return CELL_SEPARATOR.join(slots)
        if option == "saveblock" and self.selection is not None:
            start, end = self.selection
            return self.text[start:end]
        return self.text

    @property
    def PageCount(self):
        return self.pages

    def SetPos(self, _list, para, pos):
        self.cell = None
        self.cursor = max(0, int(pos) - 16)
        return True

    def GetPos(self):
        if self.cell is not None:
            # 한글 reports the cell's list index here, first cell = 2,
            # increasing row-major. Confirmed by hwp_table_probe.
            rows, columns = self.tables[0]
            row, column = self.cell
            return (1 + (row - 1) * columns + column, 0, 0)
        return (0, 0, 16 + self.cursor)

    def CreateAction(self, name):
        return FakeAction(self, name)

    def PointToHwpUnit(self, value):
        return int(round(float(value) * 100))

    def RGBColor(self, red, green, blue):
        return int(red) | (int(green) << 8) | (int(blue) << 16)

    def FindDir(self, value):
        return {"Forward": 0, "Backward": 1, "AllDoc": 2}[value]

    def SetMessageBoxMode(self, value):
        self.message_mode = int(value)


def adapter_for(hwp):
    return HwpAdapter(object_getter=lambda: hwp, enable_pdf_export=True)


def parser_for(temp_dir, hwp):
    parser = CommandParser()
    parser.execution_controller = ExecutionController(
        os.path.join(temp_dir, "diagnostics.json")
    )
    parser.preference_manager = PreferenceManager(
        os.path.join(temp_dir, "preferences.json")
    )
    parser.app_action_registry = AppActionRegistry({"hwp": adapter_for(hwp)})
    parser.action_executor.controller = parser.execution_controller
    parser.action_executor.app_action_registry = parser.app_action_registry
    parser.macro_runner.controller = parser.execution_controller
    parser.llm_engine.process_command = mock.Mock(
        side_effect=AssertionError("AI should not run")
    )
    return parser


class HwpAdapterTests(unittest.TestCase):
    def test_owned_unsaved_document_id_requires_dedicated_getter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "owned-unsaved.hwp"
            path.write_bytes(b"fixture identity")
            with self.assertRaises(ValueError):
                HwpAdapter(owned_unsaved_document_path=path)

            hwp = FakeHwp("소유 미저장 문서")
            adapter = HwpAdapter(
                object_getter=lambda: hwp,
                require_visible=False,
                owned_unsaved_document_path=path,
            )
            prepared = adapter.prepare("insert_text", {"text": " 확인"})

            self.assertEqual(
                os.path.normcase(os.path.abspath(path)), prepared.document_id
            )
            self.assertTrue(adapter.execute(prepared)["verified"])

    def test_insert_text_at_cursor_is_prepared_and_verified(self):
        hwp = FakeHwp("앞 뒤")
        hwp.cursor = 2
        adapter = adapter_for(hwp)
        prepared = adapter.prepare("insert_text", {"text": "중간"})
        result = adapter.execute(prepared)
        self.assertFalse(prepared.destructive)
        self.assertEqual("앞 중간뒤", hwp.text)
        self.assertTrue(result["verified"])
        self.assertEqual(
            {
                "bold": 0,
                "font_size_hu": 1000,
                "text_color": 0,
                "alignment": 0,
            },
            result["after"]["format"],
        )

    def test_verified_insert_can_restore_original_document_snapshot(self):
        hwp = FakeHwp("원래 문장")
        hwp.cursor = len(hwp.text)
        adapter = adapter_for(hwp)
        prepared = adapter.prepare("insert_text", {"text": " 추가"})
        result = adapter.execute(prepared)

        restored = adapter.undo(
            prepared,
            {"after_observations": result},
        )

        self.assertTrue(restored["verified"])
        self.assertEqual("원래 문장", hwp.text)

    def test_insert_over_selection_requires_confirmation_and_stale_state_blocks(self):
        hwp = FakeHwp("기존 내용")
        hwp.select(0, 2)
        adapter = adapter_for(hwp)
        prepared = adapter.prepare("insert_text", {"text": "새"})
        self.assertTrue(prepared.destructive)
        self.assertTrue(DecisionEngine().evaluate(prepared).requires_confirmation)
        hwp.text += " 변경"
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)

    def test_text_and_paragraph_formats_require_and_verify_scope(self):
        hwp = FakeHwp("제목")
        adapter = adapter_for(hwp)
        with self.assertRaises(AppActionBlocked):
            adapter.prepare("set_text_format", {"bold": True})
        hwp.select()
        text_format = adapter.prepare(
            "set_text_format", {"bold": True, "font_size": 15, "text_color": "red"}
        )
        adapter.execute(text_format)
        paragraph = adapter.prepare(
            "set_paragraph_format", {"alignment": "center"}
        )
        adapter.execute(paragraph)
        self.assertEqual(
            {"Bold": 1, "Height": 1500, "TextColor": 255}, hwp.char_state
        )
        self.assertEqual(3, hwp.paragraph_alignment)
        smaller = adapter.prepare(
            "set_text_format", {"font_size_delta": -2}
        )
        adapter.execute(smaller)
        self.assertEqual(1300, hwp.char_state["Height"])
        hwp.char_state["Height"] = 0
        with self.assertRaises(AppActionBlocked):
            adapter.prepare("set_text_format", {"font_size_delta": -2})

    def test_find_replace_respects_selection_scope(self):
        hwp = FakeHwp("홍길동 하나 / 홍길동 둘")
        hwp.select(0, 5)
        adapter = adapter_for(hwp)
        prepared = adapter.prepare(
            "find_replace",
            {"scope": "selection", "find": "홍길동", "replace": "김철수"},
        )
        result = adapter.execute(prepared)
        self.assertEqual("김철수 하나 / 홍길동 둘", hwp.text)
        self.assertEqual(1, result["after"]["replaced_count"])

    def test_read_only_document_is_blocked(self):
        with self.assertRaises(AppActionBlocked):
            adapter_for(FakeHwp("읽기", edit_mode=0)).prepare(
                "insert_text", {"text": "변경"}
            )

    def test_pdf_is_generated_and_existing_target_is_destructive(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-hwp-test-") as temp_dir:
            source = os.path.join(temp_dir, "문서.hwp")
            target = os.path.join(temp_dir, "문서.pdf")
            hwp = FakeHwp("PDF 내용", full_name=source)
            adapter = adapter_for(hwp)
            first = adapter.prepare("save_as", {"format": "PDF"})
            result = adapter.execute(first)
            self.assertTrue(result["verified"])
            self.assertTrue(os.path.isfile(target))
            second = adapter.prepare("save_as", {"format": "PDF"})
            self.assertTrue(second.destructive)

    def test_pdf_is_blocked_by_default_after_live_hang_detection(self):
        hwp = FakeHwp("PDF 내용", full_name=r"C:\\safe\\문서.hwp")
        with self.assertRaises(AppActionBlocked):
            HwpAdapter(object_getter=lambda: hwp).prepare(
                "save_as", {"path": "C:\\safe\\문서.pdf", "format": "PDF"}
            )


class HwpParserTests(unittest.TestCase):
    def test_insert_and_formats_run_locally(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-hwp-parser-") as temp_dir:
            hwp = FakeHwp()
            parser = parser_for(temp_dir, hwp)
            with mock.patch.object(command_api, "parser", parser):
                inserted = command_api.parse_command(
                    '한글 현재 커서 위치에 "JARVIS 테스트"를 입력해줘',
                    session_id="hwp-insert",
                )
                hwp.select()
                formatted = command_api.parse_command(
                    "한글 선택 영역을 굵게 하고 글자 크기를 15로 해줘",
                    session_id="hwp-format",
                )
                aligned = command_api.parse_command(
                    "한글 현재 문단을 가운데 정렬해줘",
                    session_id="hwp-para",
                )
        self.assertTrue(inserted["success"])
        self.assertTrue(formatted["success"])
        self.assertTrue(aligned["success"])
        self.assertEqual("JARVIS 테스트", hwp.text)
        self.assertEqual(1, hwp.char_state["Bold"])
        self.assertEqual(1500, hwp.char_state["Height"])
        self.assertEqual(3, hwp.paragraph_alignment)
        parser.llm_engine.process_command.assert_not_called()

    def test_explicit_find_replace_requires_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-hwp-parser-") as temp_dir:
            hwp = FakeHwp("홍길동과 홍길동")
            hwp.select()
            parser = parser_for(temp_dir, hwp)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "한글 선택 영역에서 홍길동을 김철수로 바꿔줘",
                    session_id="hwp-replace",
                )
                before = hwp.text
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "apply",
                    "hwp-replace",
                )
        self.assertEqual("confirmation_required", first["status"])
        self.assertEqual("홍길동과 홍길동", before)
        self.assertTrue(result["success"])
        self.assertEqual("김철수과 김철수", hwp.text)

    def test_ambiguous_scope_asks_and_selected_choice_runs_once(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-hwp-parser-") as temp_dir:
            hwp = FakeHwp("홍길동 / 홍길동")
            hwp.select(0, 3)
            parser = parser_for(temp_dir, hwp)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "한글 홍길동을 김철수로 바꿔줘",
                    session_id="hwp-scope",
                )
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "selection",
                    "hwp-scope",
                )
        self.assertEqual("missing_range", first["data"]["confirmation"]["reason"])
        self.assertEqual(
            "clarification", first["data"]["confirmation"]["request_kind"]
        )
        self.assertTrue(result["success"])
        self.assertEqual("김철수 / 홍길동", hwp.text)

    def test_pdf_default_path_and_overwrite_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-hwp-parser-") as temp_dir:
            source = os.path.join(temp_dir, "보고서.hwp")
            target = os.path.join(temp_dir, "보고서.pdf")
            hwp = FakeHwp("보고서", full_name=source)
            parser = parser_for(temp_dir, hwp)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "한글 현재 문서를 PDF로 저장해줘", session_id="hwp-pdf"
                )
                second = command_api.parse_command(
                    "한글 현재 문서를 PDF로 저장해줘", session_id="hwp-pdf-overwrite"
                )
                cancelled = command_api.resolve_confirmation(
                    second["data"]["confirmation"]["confirmation_id"],
                    "cancel",
                    "hwp-pdf-overwrite",
                )
                target_exists = os.path.isfile(target)
        self.assertTrue(first["success"])
        self.assertTrue(target_exists)
        self.assertEqual("confirmation_required", second["status"])
        self.assertEqual("cancelled", cancelled["status"])


class HwpLineSpacingTests(unittest.TestCase):
    """줄간격 is prescribed by Korean office templates and had no support."""

    def _adapter(self, hwp):
        return HwpAdapter(object_getter=lambda: hwp, require_visible=False)

    def test_spacing_is_applied_and_read_back(self):
        hwp = FakeHwp("첫 문단입니다.", full_name=r"C:\docs\report.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("set_line_spacing", {"line_spacing": "200"})
        self.assertFalse(prepared.noop)
        self.assertEqual(200, prepared.params["line_spacing"])
        # Preview alone must not change the document.
        self.assertEqual(160, hwp.line_spacing)

        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual(200, hwp.line_spacing)
        self.assertEqual(200, result["after"]["line_spacing"])

    def test_spoken_forms_reach_the_same_spacing(self):
        for spoken, expected in (("2배", 200), ("200%", 200), ("넓게", 200)):
            with self.subTest(spoken=spoken):
                hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
                adapter = self._adapter(hwp)
                prepared = adapter.prepare(
                    "set_line_spacing", {"line_spacing": spoken}
                )
                self.assertEqual(expected, prepared.params["line_spacing"])

    def test_requesting_the_current_spacing_changes_nothing(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("set_line_spacing", {"line_spacing": 160})
        self.assertTrue(prepared.noop)
        result = adapter.execute(prepared)
        self.assertFalse(result["changed"])
        self.assertEqual(160, hwp.line_spacing)

    def test_out_of_range_and_unreadable_wording_are_blocked(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
        adapter = self._adapter(hwp)
        for value in (10, 900, "비스듬히", None):
            with self.subTest(value=value):
                with self.assertRaises(AppActionBlocked):
                    adapter.prepare("set_line_spacing", {"line_spacing": value})
        self.assertEqual(160, hwp.line_spacing)

    def test_a_document_that_moved_after_approval_is_not_changed(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("set_line_spacing", {"line_spacing": 200})
        hwp.text = "다른 내용으로 바뀌었습니다."
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)
        self.assertEqual(160, hwp.line_spacing)

    def test_undo_restores_the_previous_spacing(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("set_line_spacing", {"line_spacing": 200})
        result = adapter.execute(prepared)
        self.assertEqual(200, hwp.line_spacing)

        restored = adapter.undo(prepared, {"after_observations": result})
        self.assertTrue(restored["verified"])
        self.assertEqual(160, hwp.line_spacing)

    def test_undo_refuses_when_the_spacing_changed_since(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs\a.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("set_line_spacing", {"line_spacing": 200})
        result = adapter.execute(prepared)
        hwp.line_spacing = 300
        with self.assertRaises(AppActionContextChanged):
            adapter.undo(prepared, {"after_observations": result})
        self.assertEqual(300, hwp.line_spacing)


class HwpDeleteTextTests(unittest.TestCase):
    def _adapter(self, hwp):
        return HwpAdapter(object_getter=lambda: hwp, require_visible=False)

    def _selected(self, text, start, end):
        hwp = FakeHwp(text, full_name=r"C:\docs.hwp")
        hwp.selection = (start, end)
        return hwp

    def test_selection_is_deleted_and_verified(self):
        hwp = self._selected("앞부분 지울내용 뒷부분", 4, 9)
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("delete_text", {})
        self.assertEqual("지울내용 ", prepared.params["original_text"])
        # Preview must not touch the document.
        self.assertEqual("앞부분 지울내용 뒷부분", hwp.text)

        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual("앞부분 뒷부분", hwp.text)
        self.assertEqual(5, result["after"]["deleted_length"])

    def test_deleting_without_a_selection_is_blocked(self):
        hwp = FakeHwp("본문", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        with self.assertRaises(AppActionBlocked):
            adapter.prepare("delete_text", {})
        self.assertEqual("본문", hwp.text)

    def test_a_document_that_moved_after_approval_is_not_deleted(self):
        hwp = self._selected("앞부분 지울내용 뒷부분", 4, 9)
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("delete_text", {})
        hwp.text = "완전히 다른 내용입니다."
        hwp.selection = (0, 3)
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)
        self.assertEqual("완전히 다른 내용입니다.", hwp.text)

    def test_repeated_text_still_verifies_exactly_one_removal(self):
        # "반복 반복 반복"[0:3] is "반복 " with the trailing space, which occurs
        # twice, so one occurrence must remain. The count proves an instance of
        # the selected text of the right length went away; which instance is
        # already pinned by the fingerprint check before execution.
        hwp = self._selected("반복 반복 반복", 0, 3)
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("delete_text", {})
        self.assertEqual("반복 ", prepared.params["original_text"])
        self.assertEqual(1, prepared.params["expected_occurrences"])
        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual("반복 반복", hwp.text)

    def test_undo_restores_the_deleted_text(self):
        hwp = self._selected("앞부분 지울내용 뒷부분", 4, 9)
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("delete_text", {})
        result = adapter.execute(prepared)
        self.assertEqual("앞부분 뒷부분", hwp.text)

        restored = adapter.undo(prepared, {"after_observations": result})
        self.assertTrue(restored["verified"])
        self.assertEqual("앞부분 지울내용 뒷부분", hwp.text)

    def test_undo_refuses_when_the_document_changed_since(self):
        hwp = self._selected("앞부분 지울내용 뒷부분", 4, 9)
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("delete_text", {})
        result = adapter.execute(prepared)
        hwp.text = "누군가 그 사이에 고쳤습니다."
        with self.assertRaises(AppActionContextChanged):
            adapter.undo(prepared, {"after_observations": result})
        self.assertEqual("누군가 그 사이에 고쳤습니다.", hwp.text)


class HwpInsertTableTests(unittest.TestCase):
    """Structure, not text: verified by counting table controls."""

    def _adapter(self, hwp):
        return HwpAdapter(object_getter=lambda: hwp, require_visible=False)

    def test_table_is_created_and_counted(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("insert_table", {"rows": 3, "columns": 4})
        self.assertEqual(0, prepared.current_state["table_count"])
        self.assertEqual("3행 4열 표", prepared.target)
        # Preview alone creates nothing.
        self.assertEqual([], hwp.tables)

        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual([(3, 4)], hwp.tables)
        self.assertEqual(1, result["after"]["table_count"])

    def test_a_second_table_counts_from_the_existing_ones(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        adapter.execute(adapter.prepare("insert_table", {"rows": 2, "columns": 2}))
        prepared = adapter.prepare("insert_table", {"rows": 5, "columns": 3})
        self.assertEqual(1, prepared.current_state["table_count"])
        result = adapter.execute(prepared)
        self.assertEqual(2, result["after"]["table_count"])
        self.assertEqual([(2, 2), (5, 3)], hwp.tables)

    def test_sizes_outside_the_safe_range_are_blocked(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        for params in (
            {"rows": 0, "columns": 3},
            {"rows": 3, "columns": 0},
            {"rows": 500, "columns": 3},
            {"rows": 3, "columns": 99},
            {"rows": "셋", "columns": 3},
            {"columns": 3},
            {"rows": 3},
        ):
            with self.subTest(params=params):
                with self.assertRaises(AppActionBlocked):
                    adapter.prepare("insert_table", params)
        self.assertEqual([], hwp.tables)

    def test_a_selection_blocks_insertion_rather_than_replacing_it(self):
        hwp = FakeHwp("지우면 안 되는 본문", full_name=r"C:\docs.hwp")
        hwp.selection = (0, 3)
        adapter = self._adapter(hwp)
        with self.assertRaises(AppActionBlocked):
            adapter.prepare("insert_table", {"rows": 2, "columns": 2})
        self.assertEqual("지우면 안 되는 본문", hwp.text)

    def test_a_document_that_moved_after_approval_is_not_changed(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("insert_table", {"rows": 2, "columns": 2})
        hwp.text = "그 사이에 바뀐 내용"
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)
        self.assertEqual([], hwp.tables)

    def test_undo_removes_the_table(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("insert_table", {"rows": 3, "columns": 4})
        result = adapter.execute(prepared)
        self.assertEqual(1, len(hwp.tables))

        restored = adapter.undo(prepared, {"after_observations": result})
        self.assertTrue(restored["verified"])
        self.assertEqual([], hwp.tables)

    def test_undo_refuses_when_the_table_is_already_gone(self):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        prepared = adapter.prepare("insert_table", {"rows": 3, "columns": 4})
        result = adapter.execute(prepared)
        hwp.tables = []
        hwp._rebuild_controls()
        with self.assertRaises(AppActionContextChanged):
            adapter.undo(prepared, {"after_observations": result})


class HwpTableCellTests(unittest.TestCase):
    """한글 has no cell accessor, so the address is reached by stepping."""

    def _adapter(self, hwp):
        return HwpAdapter(object_getter=lambda: hwp, require_visible=False)

    def _with_table(self, rows=3, columns=4):
        hwp = FakeHwp("보고서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        adapter.execute(
            adapter.prepare("insert_table", {"rows": rows, "columns": columns})
        )
        return hwp, adapter

    def test_text_lands_in_the_addressed_cell(self):
        hwp, adapter = self._with_table()
        prepared = adapter.prepare(
            "set_table_cell", {"row": 2, "column": 3, "text": "매출"}
        )
        self.assertEqual("표 2행 3열", prepared.target)
        self.assertEqual({}, hwp.cells)

        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual({(2, 3): "매출"}, hwp.cells)
        self.assertEqual("매출", result["after"]["cell_text"])

    def test_each_cell_is_addressed_independently(self):
        hwp, adapter = self._with_table()
        for row, column, text in ((1, 1, "가"), (3, 4, "나"), (2, 2, "다")):
            adapter.execute(
                adapter.prepare(
                    "set_table_cell",
                    {"row": row, "column": column, "text": text},
                )
            )
        self.assertEqual({(1, 1): "가", (3, 4): "나", (2, 2): "다"}, hwp.cells)

    def test_an_address_outside_the_table_is_blocked_before_writing(self):
        hwp, adapter = self._with_table(rows=2, columns=2)
        for params in (
            {"row": 3, "column": 1, "text": "넘침"},
            {"row": 1, "column": 5, "text": "넘침"},
        ):
            with self.subTest(params=params):
                with self.assertRaises(AppActionBlocked):
                    adapter.prepare("set_table_cell", params)
        self.assertEqual({}, hwp.cells)

    def test_writing_without_a_table_is_blocked(self):
        hwp = FakeHwp("표 없는 문서", full_name=r"C:\docs.hwp")
        adapter = self._adapter(hwp)
        with self.assertRaises(AppActionBlocked):
            adapter.prepare("set_table_cell", {"row": 1, "column": 1, "text": "x"})

    def test_writing_the_same_text_changes_nothing(self):
        hwp, adapter = self._with_table()
        adapter.execute(
            adapter.prepare(
                "set_table_cell", {"row": 1, "column": 1, "text": "같음"}
            )
        )
        prepared = adapter.prepare(
            "set_table_cell", {"row": 1, "column": 1, "text": "같음"}
        )
        self.assertTrue(prepared.noop)
        result = adapter.execute(prepared)
        self.assertFalse(result["changed"])

    def test_undo_restores_the_previous_cell_text(self):
        hwp, adapter = self._with_table()
        adapter.execute(
            adapter.prepare(
                "set_table_cell", {"row": 2, "column": 2, "text": "처음"}
            )
        )
        prepared = adapter.prepare(
            "set_table_cell", {"row": 2, "column": 2, "text": "나중"}
        )
        self.assertEqual("처음", prepared.params["original_text"])
        result = adapter.execute(prepared)
        self.assertEqual("나중", hwp.cells[(2, 2)])

        restored = adapter.undo(prepared, {"after_observations": result})
        self.assertTrue(restored["verified"])
        self.assertEqual("처음", hwp.cells[(2, 2)])

    def test_undo_refuses_when_the_cell_changed_since(self):
        hwp, adapter = self._with_table()
        prepared = adapter.prepare(
            "set_table_cell", {"row": 1, "column": 1, "text": "값"}
        )
        result = adapter.execute(prepared)
        hwp.cells[(1, 1)] = "누군가 고침"
        with self.assertRaises(AppActionContextChanged):
            adapter.undo(prepared, {"after_observations": result})
        self.assertEqual("누군가 고침", hwp.cells[(1, 1)])


if __name__ == "__main__":
    unittest.main()
