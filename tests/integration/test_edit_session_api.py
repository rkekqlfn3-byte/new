import tempfile
import unittest
from pathlib import Path

from engine.api import command_api, edit_api
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.session import EditSessionManager
from engine.parser import CommandParser


class FakeIntakeManager:
    def __init__(self, file_path):
        self.file_path = str(Path(file_path).resolve())

    def _document(self):
        return {
            "app_type": "excel",
            "file_path": self.file_path,
            "document_name": Path(self.file_path).name,
            "window_handle": 101,
            "active_container": "Sheet1",
            "selection_reference": "A1:B3",
            "launch_requested": True,
        }

    def connect_file(self, file_path):
        self.assert_path = str(Path(file_path).resolve())
        return self._document()

    def connect_active_document(self, app_type=None):
        return self._document()

    def connect_dropped_document(self, **kwargs):
        return self._document()


class FakeLayoutManager:
    enabled = True

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class EditSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "매출.xlsx"
        self.file.write_bytes(b"workbook")
        self.controller = EditModeController(
            intake_manager=FakeIntakeManager(self.file),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            file_picker=lambda: str(self.file),
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.previous_edit_parser = edit_api.parser
        self.previous_command_parser = command_api.parser
        edit_api.parser = self.parser
        command_api.parser = self.parser

    def tearDown(self):
        edit_api.parser = self.previous_edit_parser
        command_api.parser = self.previous_command_parser
        self.temp_dir.cleanup()

    def test_choose_status_request_and_disconnect_share_one_session(self):
        connected = edit_api.choose_and_connect_edit_document()
        self.assertTrue(connected["success"])
        session = connected["data"]["session"]

        status = edit_api.get_edit_session_status()
        self.assertEqual(session["session_id"], status["data"]["session"]["session_id"])

        request = command_api.parse_command(
            "이거 합계 내줘",
            mode="edit",
            session_id="chat-session",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
            },
        )
        self.assertFalse(request["success"])
        self.assertEqual("blocked", request["status"])
        self.assertEqual(session["session_id"], request["target"])
        self.assertEqual("매출.xlsx", request["data"]["document_name"])

        disconnected = edit_api.disconnect_edit_document(session["session_id"])
        self.assertTrue(disconnected["success"])
        self.assertFalse(edit_api.get_edit_session_status()["data"]["connected"])

    def test_wrong_session_fingerprint_is_blocked(self):
        session = edit_api.connect_active_edit_document("excel")["data"]["session"]
        result = command_api.parse_command(
            "수정해줘",
            mode="edit",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": "F" * 64,
            },
        )
        self.assertFalse(result["success"])
        self.assertEqual("stale_context", result["status"])


if __name__ == "__main__":
    unittest.main()
