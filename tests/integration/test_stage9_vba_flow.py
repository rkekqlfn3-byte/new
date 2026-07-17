import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.app_actions.excel_vba_adapter import ExcelVbaAdapter
from engine.edit_mode import EditModeController, EditSessionManager
from engine.parser import CommandParser
from tests.windows.test_excel_vba_actions import (
    FakeApplication,
    FakeComponent,
    FakeProject,
    FakeWorkbook,
)


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()


class Intake:
    def connect_file(self, file_path):
        path = Path(file_path).resolve()
        return {
            "app_type": "excel",
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": 9001,
            "active_container": "Sheet1",
            "selection_reference": "A1",
        }


class NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class Context:
    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": "excel",
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": "9" * 64,
            "read_only": False,
            "modified": True,
            "captured_at": "2026-07-17T10:00:00+09:00",
            "active_container": "Sheet1",
            "selection_reference": "A1",
            "selection_kind": "range",
            "target": {"sheet_name": "Sheet1", "address": "A1"},
            "selected_text_preview": "",
            "selected_text_length": 0,
            "selected_text_digest": digest(""),
            "cursor_reference": "A1",
        }


class Registry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, target):
        self.assert_excel(target)
        return self.adapter

    @staticmethod
    def assert_excel(target):
        if str(target).casefold() != "excel":
            raise AssertionError(target)


class Stage9VbaFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "stage9.xlsm"
        self.path.write_bytes(b"fixture")

    def tearDown(self):
        self.temp_dir.cleanup()

    def build(self, code):
        component = FakeComponent("Module1", code)
        workbook = FakeWorkbook(self.path, FakeProject(component))
        application = FakeApplication(workbook)
        adapter = ExcelVbaAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            discovery_retry_delay=0,
            backup_dir=Path(self.temp_dir.name) / "backups",
        )
        controller = EditModeController(
            intake_manager=Intake(),
            session_manager=EditSessionManager(),
            layout_manager=NoLayout(),
            context_manager=Context(),
            native_action_registry=Registry(adapter),
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(self.path))
        return parser, controller, session, application, component

    @staticmethod
    def command(parser, session, text, request_id):
        return parser.execute_command_result(
            text,
            mode="edit",
            session_id="stage9-chat",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": "9" * 64,
                "request_id": request_id,
            },
        )

    @staticmethod
    def approve(parser, result):
        confirmation = result["data"]["confirmation"]
        return parser.resolve_pending_confirmation(
            "stage9-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

    def test_read_modify_backup_verify_and_undo_flow(self):
        original = (
            "Public Sub FindLast()\n"
            "lastRow = Cells(Rows.Count, 1).End(xlUp).Row\n"
            "End Sub"
        )
        parser, controller, session, _, component = self.build(original)

        read = self.command(parser, session, "Module1 코드 보여줘", "vba-read")
        self.assertTrue(read["success"])
        self.assertEqual(original, read["data"]["observations"]["code"])

        preview = self.command(
            parser,
            session,
            "Module1 모듈의 마지막 행을 잘못 찾는데 고쳐줘",
            "vba-change",
        )
        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual([], component.exports)

        applied = self.approve(parser, preview)
        self.assertTrue(applied["success"])
        self.assertTrue(applied["verified"])
        self.assertTrue(Path(applied["data"]["observations"]["backup_path"]).is_file())
        self.assertIn("ActiveSheet.Rows.Count", component.CodeModule.code)
        self.assertTrue(applied["data"]["undo_available"])

        undone = self.command(parser, session, "방금 거 취소해", "vba-undo")
        self.assertTrue(undone["success"])
        self.assertEqual(original, component.CodeModule.code)
        self.assertEqual("ready", controller.status()["session"]["state"])

    def test_dangerous_macro_requires_two_approvals_and_never_runs_while_previewing(self):
        code = 'Public Sub Dangerous()\nShell("calc.exe")\nEnd Sub'
        parser, _, session, application, _ = self.build(code)

        first = self.command(
            parser,
            session,
            "Module1 모듈 Dangerous 매크로 실행해줘",
            "vba-dangerous-run",
        )
        self.assertEqual("confirmation_required", first["status"])
        self.assertEqual([], application.runs)

        second = self.approve(parser, first)
        self.assertEqual("confirmation_required", second["status"])
        self.assertIn("고위험 VBA", second["message"])
        self.assertEqual([], application.runs)

        executed = self.approve(parser, second)
        self.assertTrue(executed["success"])
        self.assertEqual(
            ["'stage9.xlsm'!Module1.Dangerous"],
            application.runs,
        )


if __name__ == "__main__":
    unittest.main()
