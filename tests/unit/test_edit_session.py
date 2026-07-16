import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.edit_mode import (
    EditAppUnavailable,
    EditDocumentAmbiguous,
    EditDocumentOpenTimeout,
    EditRequest,
    EditSessionManager,
    EditSessionBusy,
    EditSessionState,
    EditSessionStale,
    FileIntakeManager,
    UnsupportedEditDocument,
    WindowLayoutManager,
    app_type_for_path,
)
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.native_bridge import NativeDocumentBridge


class FakeBridge:
    def __init__(self):
        self.available = True
        self.existing = None
        self.waited = None
        self.active = []
        self.drop_path = None
        self.launches = []

    def is_available(self, app_type):
        return self.available

    def find_document(self, app_type, expected_path=None):
        return dict(self.existing) if self.existing else None

    def launch_document(self, app_type, file_path):
        self.launches.append((app_type, file_path))

    def wait_for_document(self, app_type, expected_path, timeout=15.0):
        return dict(self.waited) if self.waited else None

    def active_documents(self, app_type=None):
        return [
            dict(item) for item in self.active
            if not app_type or item.get("app_type") == app_type
        ]

    def resolve_explorer_selection(self, file_name, file_size=None):
        return self.drop_path


class FakeWindowBackend:
    def __init__(self):
        self.rectangles = {10: (0, 0, 10, 10), 20: (0, 0, 10, 10)}
        self.moves = []

    def is_window(self, handle):
        return handle in self.rectangles

    def find_jarvis_window(self, excluded_handle=0):
        return 20

    def work_area(self, handle):
        return (100, 50, 2100, 1050)

    def restore(self, handle):
        return None

    def move(self, handle, rect):
        self.rectangles[handle] = rect
        self.moves.append((handle, rect))

    def rect(self, handle):
        return self.rectangles[handle]


class EditFileIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.file = self.root / "매출현황.xlsx"
        self.file.write_bytes(b"test workbook")
        self.bridge = FakeBridge()
        self.manager = FileIntakeManager(self.bridge, open_timeout=1)

    def tearDown(self):
        self.temp_dir.cleanup()

    def metadata(self, path=None, app_type="excel"):
        target = Path(path or self.file).resolve()
        return {
            "app_type": app_type,
            "file_path": str(target),
            "document_name": target.name,
            "window_handle": 10,
            "active_container": "7월 실적",
            "selection_reference": "B3:F18",
        }

    def test_supported_extensions_map_to_native_apps(self):
        expected = {
            ".xlsx": "excel", ".xlsm": "excel", ".xlsb": "excel", ".xls": "excel",
            ".hwp": "hwp", ".hwpx": "hwp",
            ".docx": "word", ".docm": "word", ".doc": "word",
            ".pptx": "powerpoint", ".pptm": "powerpoint", ".ppt": "powerpoint",
        }
        for extension, app_type in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(app_type, app_type_for_path("sample" + extension))
        with self.assertRaises(UnsupportedEditDocument):
            app_type_for_path("unsafe.exe")

    def test_existing_exact_document_is_reused_without_launch(self):
        self.bridge.existing = self.metadata()
        result = self.manager.connect_file(str(self.file))
        self.assertFalse(result["launch_requested"])
        self.assertEqual([], self.bridge.launches)
        self.assertEqual("B3:F18", result["selection_reference"])

    def test_launch_requires_exact_document_rediscovery(self):
        self.bridge.waited = self.metadata()
        result = self.manager.connect_file(str(self.file))
        self.assertTrue(result["launch_requested"])
        self.assertEqual("excel", self.bridge.launches[0][0])

        other = self.root / "다른문서.xlsx"
        other.write_bytes(b"other")
        self.bridge.waited = self.metadata(other)
        with self.assertRaises(EditDocumentOpenTimeout):
            self.manager.connect_file(str(self.file))

    def test_missing_app_and_open_timeout_are_explicit(self):
        self.bridge.available = False
        with self.assertRaises(EditAppUnavailable):
            self.manager.connect_file(str(self.file))
        self.assertEqual([], self.bridge.launches)

        self.bridge.available = True
        with self.assertRaises(EditDocumentOpenTimeout):
            self.manager.connect_file(str(self.file))

    def test_active_document_requires_one_unambiguous_supported_target(self):
        word = self.root / "보고서.docx"
        word.write_bytes(b"word")
        self.bridge.active = [self.metadata(), self.metadata(word, "word")]
        with self.assertRaises(EditDocumentAmbiguous) as raised:
            self.manager.connect_active_document()
        self.assertEqual(2, len(raised.exception.candidates))

        result = self.manager.connect_active_document("word")
        self.assertEqual("word", result["app_type"])

    def test_drop_uses_exact_path_hint_or_explorer_selection(self):
        self.bridge.existing = self.metadata()
        direct = self.manager.connect_dropped_document(
            file_name=self.file.name,
            file_size=self.file.stat().st_size,
            path_hint=str(self.file),
        )
        self.assertEqual(self.file.name, direct["document_name"])

        self.bridge.drop_path = str(self.file)
        resolved = self.manager.connect_dropped_document(
            file_name=self.file.name,
            file_size=self.file.stat().st_size,
        )
        self.assertEqual(self.file.name, resolved["document_name"])

    def test_powerpoint_late_bound_hwnd_methods_are_normalized(self):
        window = SimpleNamespace(HWND=lambda: 4321)
        slide = SimpleNamespace(SlideIndex=lambda: 2)
        selection = SimpleNamespace(Type=lambda: 3)
        document = SimpleNamespace(
            FullName=str(self.file),
            Name=self.file.name,
            Windows=SimpleNamespace(Item=lambda index: window),
        )
        application = SimpleNamespace(
            ActivePresentation=document,
            ActiveWindow=SimpleNamespace(
                HWND=lambda: 4321,
                View=SimpleNamespace(Slide=slide),
                Selection=selection,
            ),
        )
        metadata = NativeDocumentBridge._office_metadata(
            "powerpoint", application, document
        )
        self.assertEqual(4321, metadata["window_handle"])
        self.assertEqual("슬라이드 2", metadata["active_container"])
        self.assertEqual("selection:3", metadata["selection_reference"])


class EditSessionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "보고서.docx"
        self.file.write_bytes(b"document")
        self.manager = EditSessionManager()
        self.session = self.manager.connect({
            "app_type": "word",
            "file_path": str(self.file),
            "document_name": self.file.name,
            "window_handle": 22,
            "selection_reference": "10:20",
        })

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_is_json_only_and_request_is_bound_to_identity(self):
        encoded = json.dumps(self.manager.current(), ensure_ascii=False)
        self.assertIn("보고서.docx", encoded)
        request = EditRequest(
            text="이 문단을 줄여줘",
            edit_session_id=self.session["session_id"],
            document_fingerprint=self.session["document_fingerprint"],
        )
        validated = self.manager.validate_request(request)
        self.assertEqual(self.session["session_id"], validated.session_id)

        wrong = EditRequest(
            text="이 문단을 줄여줘",
            edit_session_id="edit-wrong-session",
            document_fingerprint=self.session["document_fingerprint"],
        )
        with self.assertRaises(EditSessionStale):
            self.manager.validate_request(wrong)

    def test_disconnect_does_not_close_or_delete_document(self):
        disconnected = self.manager.disconnect(self.session["session_id"])
        self.assertEqual("disconnected", disconnected["state"])
        self.assertTrue(self.file.exists())
        self.assertIsNone(self.manager.current())

    def test_replacement_is_blocked_while_an_edit_is_preparing(self):
        machine = self.manager.state_machine_for(self.session["session_id"])
        machine.transition(EditSessionState.PREPARING, reason="test edit")
        with self.assertRaises(EditSessionBusy):
            self.manager.assert_connectable()


class WindowLayoutManagerTests(unittest.TestCase):
    def test_arranges_once_on_document_monitor_and_then_respects_user_move(self):
        backend = FakeWindowBackend()
        manager = WindowLayoutManager(backend=backend, document_ratio=0.68)
        result = manager.arrange("edit-session-1", 10)
        self.assertTrue(result["success"])
        self.assertEqual((100, 50, 1460, 1050), backend.rect(10))
        self.assertEqual((1460, 50, 2100, 1050), backend.rect(20))
        self.assertEqual(2, len(backend.moves))

        second = manager.arrange("edit-session-1", 10)
        self.assertEqual("already_arranged", second["status"])
        self.assertEqual(2, len(backend.moves))

    def test_layout_is_optional_and_failure_is_non_throwing(self):
        backend = FakeWindowBackend()
        manager = WindowLayoutManager(backend=backend, enabled=False)
        self.assertEqual("disabled", manager.arrange("session", 10)["status"])
        manager.set_enabled(True)
        result = manager.arrange("session", 999)
        self.assertFalse(result["success"])
        self.assertEqual("skipped", result["status"])

    def test_auto_layout_preference_persists_outside_browser_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "edit_mode_settings.json"
            first = EditModeController(settings_path=settings_path)
            first.set_auto_layout(False)
            second = EditModeController(settings_path=settings_path)
            self.assertFalse(second.status()["auto_layout"])


if __name__ == "__main__":
    unittest.main()
