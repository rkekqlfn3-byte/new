import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.app_actions import PreparedAction
from engine.api import command_api, edit_api
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.session import EditSessionManager
from engine.parser import CommandParser
from engine.learning import UserPreferenceLearningManager


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


class FakeContextManager:
    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": value["app_type"],
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": "C" * 64,
            "active_container": "Sheet1",
            "selection_reference": "A1:A3",
            "selection_kind": "range",
            "target": {"sheet_name": "Sheet1", "address": "A1:A3"},
            "selected_text_preview": "10 · 20",
            "selected_text_length": 7,
            "selected_text_digest": "D" * 64,
            "cursor_reference": "A1:A3",
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-16T12:00:00+09:00",
        }


class FakeExcelAdapter:
    def __init__(self, file_path):
        self.file_path = str(Path(file_path).resolve())

    def prepare(self, operation, params):
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=self.file_path,
            workbook_name=Path(self.file_path).name,
            sheet="Sheet1",
            target=params.get("target_cell", "A4"),
            params=dict(params),
            current_state={"value": None, "formula": None},
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def execute(self, prepared):
        return {"success": True, "verified": True, "changed": True}


class FakeRegistry:
    def __init__(self, file_path):
        self.adapter = FakeExcelAdapter(file_path)

    def get(self, target):
        return self.adapter


class EditSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "매출.xlsx"
        self.file.write_bytes(b"workbook")
        self.controller = EditModeController(
            intake_manager=FakeIntakeManager(self.file),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=FakeContextManager(),
            native_action_registry=FakeRegistry(self.file),
            file_picker=lambda: str(self.file),
            user_learning_manager=UserPreferenceLearningManager(
                Path(self.temp_dir.name) / "user-style-preferences.json"
            ),
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
        self.assertEqual("A1:A3", status["data"]["context"]["selection_reference"])

        context = edit_api.get_edit_context(session["session_id"])
        self.assertTrue(context["success"])
        self.assertEqual("C" * 64, context["data"]["context"]["context_fingerprint"])

        request = command_api.parse_command(
            "이거 합계 내줘",
            mode="edit",
            session_id="chat-session",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": "C" * 64,
            },
        )
        self.assertFalse(request["success"])
        self.assertEqual("confirmation_required", request["status"])
        self.assertIn("합계", request["message"])
        confirmation = request["data"]["confirmation"]
        cancelled = command_api.resolve_confirmation(
            confirmation["confirmation_id"],
            "cancel",
            session_id="chat-session",
        )
        self.assertEqual("user_cancelled", cancelled["error_type"])

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
                "context_fingerprint": "C" * 64,
            },
        )
        self.assertFalse(result["success"])
        self.assertEqual("stale_context", result["status"])

    def test_user_preference_learning_status_is_local_and_explicit(self):
        result = edit_api.get_user_preference_learning_status()
        self.assertTrue(result["success"])
        self.assertEqual([], result["data"]["active_preferences"])
        self.assertEqual([], result["data"]["candidates"])

    def test_excel_vba_trust_status_api_is_read_only(self):
        with mock.patch.object(
            edit_api,
            "vba_trust_status",
            return_value={
                "status": "disabled",
                "configured": True,
                "runtime_check_required": True,
                "security_setting_changed": False,
            },
        ):
            result = edit_api.get_excel_vba_trust_status()

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("disabled", result["data"]["status"])
        self.assertFalse(result["data"]["security_setting_changed"])


if __name__ == "__main__":
    unittest.main()
