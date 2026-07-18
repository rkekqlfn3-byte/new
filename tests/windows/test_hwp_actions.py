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
        return True

    def Execute(self, name, parameter_set):
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
        if name == "SelectAll":
            self.hwp.selection = (0, len(self.hwp.text))
            return True
        if name == "Cancel":
            self.hwp.selection = None
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
        self.message_mode = 0xF0000
        self.undo_stack = []
        self.HParameterSet = SimpleNamespace(
            HCharShape=FakeShapeSet(Bold=0, Height=1000, TextColor=0),
            HParaShape=FakeShapeSet(AlignType=0),
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
        self.HAction = FakeHAction(self)

    def _push_undo(self):
        self.undo_stack.append((
            self.text,
            self.cursor,
            self.selection,
            dict(self.char_state),
            self.paragraph_alignment,
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
                self.IsModified,
            ) = self.undo_stack.pop()

    def select(self, start=0, end=None):
        self.selection = (start, len(self.text) if end is None else end)

    def GetSelectedPos(self):
        if self.selection is None:
            return (False, 0, 0, 0, 0, 0, 0)
        start, end = self.selection
        return (True, 0, 0, 16 + start, 0, 0, 16 + end)

    def GetTextFile(self, _format, option):
        if option == "saveblock" and self.selection is not None:
            start, end = self.selection
            return self.text[start:end]
        return self.text

    def GetPos(self):
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
        hwp = FakeHwp("PDF 내용", full_name="C:\\safe\\문서.hwp")
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
        self.assertEqual("ambiguous_scope", first["data"]["confirmation"]["reason"])
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


if __name__ == "__main__":
    unittest.main()
