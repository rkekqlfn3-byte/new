import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.edit_mode.context import (
    EditContextBusy,
    EditContextInactive,
    EditContextManager,
    NativeDocumentContextReader,
    default_context_providers,
)
from engine.edit_mode.native_bridge import NativeOfficeBusy
from engine.edit_mode.session import (
    document_identity_fingerprint,
    runtime_document_identity_fingerprint,
)
from verification.prototype1_stage4_probe import _probe_app


class FakeReader:
    def __init__(self, file_path):
        self.file_path = str(Path(file_path).resolve())
        self.address = "B3:F18"
        self.text = "7월 실적"

    def capture(self, app_type, expected_path):
        return {
            "app_type": app_type,
            "file_path": self.file_path,
            "document_name": Path(self.file_path).name,
            "active_container": "7월 실적",
            "selection_reference": self.address,
            "selection_kind": "range",
            "target": {"sheet_name": "7월 실적", "address": self.address},
            "selected_text": self.text,
            "cursor_reference": self.address,
            "read_only": False,
            "modified": False,
        }


class EditContextManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "매출현황.xlsx"
        self.file.write_bytes(b"workbook")
        self.reader = FakeReader(self.file)
        self.manager = EditContextManager(
            providers=default_context_providers(self.reader)
        )
        self.session = {
            "session_id": "edit-context-test",
            "app_type": "excel",
            "file_path": str(self.file),
            "document_name": self.file.name,
            "document_fingerprint": document_identity_fingerprint(
                str(self.file), "excel"
            ),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_same_selection_is_stable_and_snapshot_is_json_only(self):
        first = self.manager.capture(self.session)
        second = self.manager.capture(self.session)
        self.assertEqual(first["context_fingerprint"], second["context_fingerprint"])
        self.assertEqual("7월 실적", first["active_container"])
        self.assertEqual("B3:F18", first["selection_reference"])
        self.assertEqual("7월 실적", first["selected_text_preview"])
        json.dumps(first, ensure_ascii=False)

    def test_selected_text_tone_is_labeled_without_storing_extra_content(self):
        self.reader.text = "매출을 정리해 보고드립니다. 검토 부탁드립니다."
        context = self.manager.capture(self.session)
        self.assertEqual("formal", context["selected_text_tone"])
        self.reader.text = "숫자만 다시 볼게요. 내일 공유해요."
        self.assertEqual(
            "friendly",
            self.manager.capture(self.session)["selected_text_tone"],
        )

    def test_selection_or_selected_text_change_updates_context_fingerprint(self):
        first = self.manager.capture(self.session)
        self.reader.address = "G3:G18"
        moved = self.manager.capture(self.session)
        self.assertNotEqual(first["context_fingerprint"], moved["context_fingerprint"])

        self.reader.address = "B3:F18"
        self.reader.text = "변경된 실적"
        changed = self.manager.capture(self.session)
        self.assertNotEqual(first["context_fingerprint"], changed["context_fingerprint"])

    def test_other_document_path_and_replaced_file_are_blocked(self):
        other = Path(self.temp_dir.name) / "다른문서.xlsx"
        other.write_bytes(b"other")
        self.reader.file_path = str(other.resolve())
        with self.assertRaises(EditContextInactive):
            self.manager.capture(self.session)

        self.reader.file_path = str(self.file.resolve())
        self.file.unlink()
        self.file.write_bytes(b"replacement")
        with self.assertRaises(EditContextInactive):
            self.manager.capture(self.session)

    def test_unsaved_excel_context_uses_runtime_identity_and_detects_save(self):
        class RuntimeProvider:
            app_type = "excel"

            def __init__(self):
                self.handle = 77
                self.saved_path = ""

            def capture(self, session):
                return {
                    "app_type": "excel",
                    "file_path": self.saved_path,
                    "document_name": "통합 문서1",
                    "runtime_document_id": "RUNTIME-77",
                    "window_handle": self.handle,
                    "active_container": "Sheet1",
                    "selection_reference": "A1",
                    "selection_kind": "range",
                    "target": {"sheet_name": "Sheet1", "address": "A1"},
                    "selected_text": "",
                    "cursor_reference": "A1",
                    "read_only": False,
                    "modified": True,
                }

        provider = RuntimeProvider()
        manager = EditContextManager(providers=(provider,))
        session = {
            "session_id": "edit-runtime-test",
            "app_type": "excel",
            "file_path": "",
            "document_name": "통합 문서1",
            "identity_kind": "runtime",
            "runtime_document_id": "RUNTIME-77",
            "window_handle": 77,
            "document_fingerprint": runtime_document_identity_fingerprint(
                "excel", "RUNTIME-77", 77
            ),
        }

        context = manager.capture(session)
        self.assertEqual("", context["file_path"])
        self.assertFalse(context["document_saved"])

        saved = Path(self.temp_dir.name) / "저장됨.xlsx"
        saved.write_bytes(b"saved")
        provider.saved_path = str(saved)
        transitioned = manager.capture(session)
        self.assertTrue(transitioned["document_saved"])
        self.assertEqual(str(saved.resolve()).casefold(), transitioned["file_path"].casefold())

        provider.saved_path = ""
        provider.handle = 88
        with self.assertRaises(EditContextInactive):
            manager.capture(session)


class NativeContextExtractionTests(unittest.TestCase):
    def test_office_busy_context_retries_then_preserves_selected_address(self):
        class FlakyReader(NativeDocumentContextReader):
            def __init__(self, failures):
                runtime = SimpleNamespace(
                    CoInitialize=lambda: None,
                    CoUninitialize=lambda: None,
                )
                super().__init__(
                    runtime,
                    busy_retry_attempts=3,
                    busy_retry_delay=0,
                )
                self.failures = failures
                self.attempts = 0

            def _capture_office_document(self, app_type, expected_path):
                self.attempts += 1
                if self.attempts <= self.failures:
                    raise NativeOfficeBusy("Excel busy")
                return {
                    "app_type": app_type,
                    "file_path": expected_path,
                    "selection_reference": "G16",
                }

        recovered = FlakyReader(failures=2)
        context = recovered.capture("excel", "C:/test.xlsx")
        self.assertEqual("G16", context["selection_reference"])
        self.assertEqual(3, recovered.attempts)

        blocked = FlakyReader(failures=10)
        with self.assertRaises(EditContextBusy) as raised:
            blocked.capture("excel", "C:/test.xlsx")
        self.assertEqual(3, blocked.attempts)
        self.assertEqual("busy", raised.exception.status)
        self.assertTrue(raised.exception.retryable)

    def test_excel_range_context_contains_sheet_address_and_small_value_preview(self):
        selection = SimpleNamespace(
            Address=lambda row_absolute, column_absolute: "$B$3:$F$18",
            CountLarge=4,
            Value2=((10, 20), (30, 40)),
        )
        application = SimpleNamespace(
            ActiveSheet=SimpleNamespace(Name="7월 실적"),
            Selection=selection,
        )
        document = SimpleNamespace(
            FullName="C:/test.xlsx",
            Name="test.xlsx",
            ReadOnly=False,
            Saved=True,
        )
        context = NativeDocumentContextReader._capture_excel(application, document)
        self.assertEqual("B3:F18", context["selection_reference"])
        self.assertEqual("7월 실적", context["active_container"])
        self.assertEqual("10 · 20 · 30 · 40", context["selected_text"])

    def test_word_range_context_contains_offsets_and_table_cell(self):
        cell = SimpleNamespace(RowIndex=2, ColumnIndex=3)

        class Selection:
            Start = 10
            End = 24
            Text = "선택한 문단"
            Cells = SimpleNamespace(Item=lambda index: cell)

            @staticmethod
            def Information(code):
                return {3: 4, 12: True}[code]

        document = SimpleNamespace(
            FullName="C:/report.docx",
            Name="report.docx",
            ReadOnly=False,
            Saved=False,
        )
        context = NativeDocumentContextReader._capture_word(
            SimpleNamespace(Selection=Selection()), document
        )
        self.assertEqual("페이지 4", context["active_container"])
        self.assertEqual("table_cell", context["selection_kind"])
        self.assertEqual("표 R2C3 · 10:24", context["selection_reference"])

    def test_powerpoint_text_context_contains_slide_shape_and_text_range(self):
        text_range = SimpleNamespace(Start=2, Length=5, Text="제목")
        shape = SimpleNamespace(Id=7, Name="제목 1")
        selection = SimpleNamespace(
            Type=3,
            ShapeRange=SimpleNamespace(Count=1, Item=lambda index: shape),
            TextRange=text_range,
        )
        window = SimpleNamespace(
            View=SimpleNamespace(Slide=SimpleNamespace(SlideIndex=3)),
            Selection=selection,
        )
        document = SimpleNamespace(
            FullName="C:/deck.pptx",
            Name="deck.pptx",
            ReadOnly=False,
            Saved=True,
        )
        context = NativeDocumentContextReader._capture_powerpoint(
            SimpleNamespace(ActiveWindow=window), document
        )
        self.assertEqual("슬라이드 3", context["active_container"])
        self.assertIn("제목 1", context["selection_reference"])
        self.assertEqual(7, context["target"]["shape_id"])
        self.assertEqual(1, context["target"]["shape_count"])
        self.assertEqual("제목", context["selected_text"])


class Stage4ProbeContractTests(unittest.TestCase):
    def test_active_document_probe_returns_stable_json_context_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "probe.xlsx"
            path.write_bytes(b"fixture")

            class Bridge:
                @staticmethod
                def active_documents(app_type):
                    return [{"file_path": str(path)}]

            class Manager:
                @staticmethod
                def capture(session):
                    return {
                        "context_fingerprint": "A" * 64,
                        "selection_kind": "range",
                        "selection_reference": "B3:F18",
                        "selected_text_preview": "",
                    }

            result = _probe_app(Bridge(), Manager(), "excel")
            self.assertEqual("passed", result["status"])
            self.assertTrue(result["same_selection_stable"])
            self.assertTrue(result["json_only"])


if __name__ == "__main__":
    unittest.main()
