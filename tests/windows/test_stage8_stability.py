import gc
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.app_actions.base import AppActionBlocked
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter
from tests.windows.test_excel_native_write import (
    FakeExcel,
    FakeSheet,
    adapter_for as excel_adapter_for,
)
from tests.windows.test_hwp_actions import FakeHwp, adapter_for as hwp_adapter_for
from tests.windows.test_word_powerpoint_actions import (
    Collection,
    FakePptApplication,
    FakePresentation,
    FakeShape,
    FakeSlide,
    FakeWordApplication,
    FakeWordDocument,
)


class PrototypeStage8StabilityTests(unittest.TestCase):
    def test_powerpoint_retries_only_transient_busy_com_errors(self):
        adapter = PowerPointAdapter()
        calls = []

        def temporarily_busy():
            calls.append(True)
            if len(calls) < 3:
                raise OSError(-2147417846, "application is busy")
            return "ready"

        with mock.patch("engine.app_actions.powerpoint_adapter.time.sleep"):
            self.assertEqual("ready", adapter._retry_transient_com(temporarily_busy))
        self.assertEqual(3, len(calls))

        with self.assertRaises(OSError):
            adapter._retry_transient_com(lambda: (_ for _ in ()).throw(OSError(5)))

    def test_powerpoint_finds_nested_transient_hresult(self):
        error = Exception(
            -2147352567,
            "PowerPoint exception",
            (0, "Microsoft PowerPoint", "no active presentation", "", 0, -2147188160),
        )
        self.assertEqual(-2147188160, PowerPointAdapter._com_error_code(error))

    def test_powerpoint_retries_disconnected_proxy_only_before_write(self):
        adapter = PowerPointAdapter()
        calls = []

        def disconnected_then_ready():
            calls.append(True)
            if len(calls) < 3:
                raise OSError(-2147417848, "stale dispatch proxy")
            return "ready"

        with mock.patch("engine.app_actions.powerpoint_adapter.time.sleep"):
            self.assertEqual(
                "ready",
                adapter._retry_transient_com(
                    disconnected_then_ready,
                    include_proxy_reacquire=True,
                ),
            )
        self.assertEqual(3, len(calls))

        calls.clear()

        def disconnected_after_write():
            calls.append(True)
            raise OSError(-2147417848, "write already started")

        with self.assertRaises(OSError):
            adapter._retry_transient_com(
                disconnected_after_write,
                include_proxy_reacquire=True,
                write_started=lambda: True,
            )
        self.assertEqual(1, len(calls))

    def test_powerpoint_reacquires_selected_shape_by_stable_ids(self):
        class IdOnlySelectionProxy:
            def __init__(self, shape_id):
                self.Id = shape_id

            def __getattr__(self, name):
                raise OSError(-2147417848, f"stale selection proxy: {name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage8-reacquire.pptx"
            path.write_bytes(b"fixture")
            shape = FakeShape(7, "제목 1", "프록시 재획득")
            slide = FakeSlide(1, 100, shape)
            presentation = FakePresentation(path, [slide])
            application = FakePptApplication(presentation, slide, shape)
            application.ActiveWindow.Selection.ShapeRange = Collection(
                IdOnlySelectionProxy(shape.Id)
            )
            adapter = PowerPointAdapter(application_getter=lambda: application)

            prepared = adapter.prepare(
                "move_shape",
                {"document_path": str(path), "dx": 1, "dy": 0},
            )
            result = adapter.execute(prepared)

            self.assertTrue(result["verified"])
            self.assertEqual(51.0, shape.Left)

    def test_powerpoint_resize_rolls_back_before_retrying_busy_write(self):
        class FailOnceWidthShape(FakeShape):
            def __init__(self, *args, **kwargs):
                self._width = 0.0
                self.fail_next_width = False
                super().__init__(*args, **kwargs)

            @property
            def Width(self):
                return self._width

            @Width.setter
            def Width(self, value):
                if self.fail_next_width:
                    self.fail_next_width = False
                    raise OSError(-2147417846, "PowerPoint busy during Width")
                self._width = float(value)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage8-resize-retry.pptx"
            path.write_bytes(b"fixture")
            shape = FailOnceWidthShape(7, "제목 1", "크기 재시도")
            slide = FakeSlide(1, 100, shape)
            presentation = FakePresentation(path, [slide])
            application = FakePptApplication(presentation, slide, shape)
            adapter = PowerPointAdapter(application_getter=lambda: application)
            prepared = adapter.prepare(
                "resize_shape",
                {"document_path": str(path), "scale": 1.1},
            )
            shape.fail_next_width = True

            with mock.patch("engine.app_actions.powerpoint_adapter.time.sleep"):
                result = adapter.execute(prepared)

            self.assertTrue(result["verified"])
            self.assertAlmostEqual(330.0, shape.Width)
            self.assertAlmostEqual(55.0, shape.Height)
            self.assertEqual(-1, shape.LockAspectRatio)

    def test_powerpoint_item_error_never_falls_back_to_another_dispatch(self):
        class BusyCollection:
            fallback_called = False

            @staticmethod
            def Item(index):
                raise OSError(-2147417846, f"busy at {index}")

            def __call__(self, index):
                self.fallback_called = True
                return object()

        collection = BusyCollection()
        with self.assertRaises(OSError):
            PowerPointAdapter._item(collection, 1)
        self.assertFalse(collection.fallback_called)

    def test_excel_adapter_verifies_100_consecutive_commands(self):
        excel = FakeExcel()
        adapter = excel_adapter_for(excel)
        verified = 0
        for index in range(100):
            prepared = adapter.prepare(
                "write_cell",
                {"cell": "A1", "value": index, "value_type": "number"},
            )
            verified += int(adapter.execute(prepared)["verified"])
        gc.collect()
        self.assertEqual(100, verified)
        self.assertEqual(99, excel.ActiveSheet.Range("A1").Value2)

    def test_hwp_adapter_verifies_100_consecutive_commands(self):
        hwp = FakeHwp("100회 안정성 검증 문장")
        hwp.select()
        adapter = hwp_adapter_for(hwp)
        verified = 0
        for index in range(100):
            prepared = adapter.prepare(
                "set_text_format",
                {"bold": bool(index % 2)},
            )
            verified += int(adapter.execute(prepared)["verified"])
        gc.collect()
        self.assertEqual(100, verified)
        self.assertEqual(1, hwp.char_state["Bold"])

    def test_word_adapter_verifies_100_consecutive_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage8.docx"
            path.write_bytes(b"fixture")
            document = FakeWordDocument(path, "100회 안정성 검증 제목")
            application = FakeWordApplication(document, 0, len(document.text))
            adapter = WordAdapter(application_getter=lambda: application)
            verified = 0
            for index in range(100):
                prepared = adapter.prepare(
                    "set_text_format",
                    {
                        "document_path": str(path),
                        "font_size": 10 if index % 2 == 0 else 12,
                    },
                )
                verified += int(adapter.execute(prepared)["verified"])
            gc.collect()
            self.assertEqual(100, verified)
            self.assertEqual(12.0, document.font.Size)

    def test_powerpoint_adapter_verifies_100_consecutive_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage8.pptx"
            path.write_bytes(b"fixture")
            shape = FakeShape(7, "제목 1", "100회 안정성 검증 제목")
            slide = FakeSlide(1, 100, shape)
            presentation = FakePresentation(path, [slide])
            application = FakePptApplication(presentation, slide, shape)
            adapter = PowerPointAdapter(application_getter=lambda: application)
            verified = 0
            for index in range(100):
                prepared = adapter.prepare(
                    "move_shape",
                    {
                        "document_path": str(path),
                        "dx": 1 if index % 2 == 0 else -1,
                        "dy": 0,
                    },
                )
                verified += int(adapter.execute(prepared)["verified"])
            gc.collect()
            self.assertEqual(100, verified)
            self.assertEqual(50.0, shape.Left)

    def test_all_four_adapters_reject_read_only_or_protected_documents(self):
        protected_excel = FakeExcel(sheet=FakeSheet(protected=True))
        with self.assertRaises(AppActionBlocked):
            excel_adapter_for(protected_excel).prepare(
                "write_cell",
                {"cell": "A1", "value": 1},
            )

        with self.assertRaises(AppActionBlocked):
            hwp_adapter_for(FakeHwp("읽기 전용", edit_mode=0)).prepare(
                "insert_text",
                {"text": "변경"},
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            word_path = Path(temp_dir) / "readonly.docx"
            word_path.write_bytes(b"fixture")
            word = FakeWordDocument(word_path, "읽기 전용")
            word.ReadOnly = True
            word_app = FakeWordApplication(word, 0, len(word.text))
            with self.assertRaises(AppActionBlocked):
                WordAdapter(application_getter=lambda: word_app).prepare(
                    "set_text_format",
                    {"document_path": str(word_path), "bold": True},
                )

            ppt_path = Path(temp_dir) / "readonly.pptx"
            ppt_path.write_bytes(b"fixture")
            shape = FakeShape(7, "제목", "읽기 전용")
            slide = FakeSlide(1, 100, shape)
            presentation = FakePresentation(ppt_path, [slide])
            presentation.ReadOnly = True
            ppt_app = FakePptApplication(presentation, slide, shape)
            with self.assertRaises(AppActionBlocked):
                PowerPointAdapter(application_getter=lambda: ppt_app).prepare(
                    "move_shape",
                    {"document_path": str(ppt_path), "dx": 1, "dy": 0},
                )


if __name__ == "__main__":
    unittest.main()
