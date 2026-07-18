import tempfile
import unittest
from pathlib import Path

from engine.app_actions import PreparedAction
from engine.edit_mode import (
    EditContextInactive,
    EditModeController,
    EditSessionManager,
)
from engine.parser import CommandParser


class FakeIntakeManager:
    def __init__(self, path):
        self.path = str(Path(path).resolve())

    def connect_file(self, file_path):
        return {
            "app_type": "excel",
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": 10,
            "active_container": "Sheet1",
            "selection_reference": "A2",
        }


class FakeLayoutManager:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class MutableContextManager:
    def __init__(self):
        self.context_fingerprint = "C" * 64
        self.address = "A2"
        self.inactive_captures = 0
        self.document_fingerprint_override = None

    def capture(self, session):
        if self.inactive_captures > 0:
            self.inactive_captures -= 1
            raise EditContextInactive(
                "연결된 문서가 현재 활성 문서가 아닙니다."
            )
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": "excel",
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": (
                self.document_fingerprint_override
                or value["document_fingerprint"]
            ),
            "context_fingerprint": self.context_fingerprint,
            "active_container": "Sheet1",
            "selection_reference": self.address,
            "selection_kind": "range",
            "target": {"sheet_name": "Sheet1", "address": self.address},
            "selected_text_preview": "10",
            "selected_text_length": 2,
            "selected_text_digest": "D" * 64,
            "cursor_reference": self.address,
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-16T12:00:00+09:00",
        }


class FakeExcelAdapter:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.executed = 0
        self.wrong_document = False

    def prepare(self, operation, params):
        target = params.get("cell") or params.get("range") or params.get("source_range") or "A2"
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=(self.path + ".other" if self.wrong_document else self.path),
            workbook_name=Path(self.path).name,
            sheet="Sheet1",
            target=target,
            params=dict(params),
            current_state={"value": 10, "formula": None},
            estimated_changes=1,
            destructive=True,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def execute(self, prepared):
        self.executed += 1
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": prepared.operation,
            "target": prepared.target,
        }


class FakeRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, target):
        return self.adapter


class FakeWindowActivator:
    def __init__(self):
        self.success = True
        self.calls = []

    def activate(self, handle):
        self.calls.append(int(handle))
        return {
            "success": self.success,
            "status": "focused" if self.success else "focus_rejected",
            "focused": self.success,
        }


class Stage5EditFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "stage5.xlsx"
        self.file.write_bytes(b"fixture")
        self.context = MutableContextManager()
        self.native = FakeExcelAdapter(self.file)
        self.activator = FakeWindowActivator()
        self.controller = EditModeController(
            intake_manager=FakeIntakeManager(self.file),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=self.context,
            native_action_registry=FakeRegistry(self.native),
            window_activator=self.activator,
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.session = self.controller.connect_file(str(self.file))
        self.activator.calls.clear()

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(self, text, request_id):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id="chat-stage5",
            edit_context={
                "edit_session_id": self.session["session_id"],
                "document_fingerprint": self.session["document_fingerprint"],
                "context_fingerprint": self.context.context_fingerprint,
                "request_id": request_id,
            },
        )

    def test_preview_approval_execute_verify_and_return_ready(self):
        preview = self.command("42 입력해줘", "request-apply")

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("변경 후: 42", preview["message"])
        self.assertEqual("approval_required", self.controller.status()["session"]["state"])
        confirmation = preview["data"]["confirmation"]

        result = self.parser.resolve_pending_confirmation(
            "chat-stage5",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(1, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_edit_request_focuses_and_recaptures_same_connected_document_once(self):
        self.context.inactive_captures = 1

        preview = self.command("42 입력해줘", "request-focus-recovery")

        self.assertEqual("confirmation_required", preview["status"])
        recovery = preview["data"]["automatic_recovery"]
        self.assertEqual("connected_document_focus", recovery["strategy"])
        self.assertEqual("recovered", recovery["outcome"])
        self.assertEqual("pre_execution", recovery["phase"])
        self.assertFalse(recovery["execution_started"])
        self.assertTrue(recovery["target_unchanged"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertEqual(1, recovery["retry_limit"])
        self.assertEqual([10], self.activator.calls)
        self.assertEqual(0, self.native.executed)
        self.assertIn("같은 문서와 현재 선택을 다시 확인", preview["message"])

    def test_approval_focus_recovery_happens_before_native_execution(self):
        preview = self.command("42 입력해줘", "request-approval-focus")
        confirmation = preview["data"]["confirmation"]
        self.context.inactive_captures = 1

        result = self.parser.resolve_pending_confirmation(
            "chat-stage5",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

        self.assertTrue(result["success"])
        self.assertEqual("recovered", result["data"]["automatic_recovery"]["outcome"])
        self.assertFalse(
            result["data"]["automatic_recovery"]["execution_started"]
        )
        self.assertEqual([10], self.activator.calls)
        self.assertEqual(1, self.native.executed)

    def test_focus_rejection_returns_retryable_environment_guidance(self):
        self.context.inactive_captures = 1
        self.activator.success = False

        result = self.command("42 입력해줘", "request-focus-rejected")

        self.assertFalse(result["success"])
        self.assertEqual("unavailable", result["data"]["automatic_recovery"]["outcome"])
        self.assertTrue(result["retryable"])
        self.assertEqual([10], self.activator.calls)
        self.assertEqual(0, self.native.executed)
        self.assertIn("문서 창을 한 번 눌러", result["message"])

    def test_changed_document_after_focus_is_blocked_before_preview(self):
        self.context.inactive_captures = 1
        self.context.document_fingerprint_override = "F" * 64

        result = self.command("42 입력해줘", "request-focus-target-changed")

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("target_changed", recovery["outcome"])
        self.assertFalse(recovery["target_unchanged"])
        self.assertFalse(recovery["execution_started"])
        self.assertEqual(0, self.native.executed)
        self.assertIn("현재 문서를 다시 연결", result["message"])

    def test_status_polling_never_activates_document_window(self):
        self.context.inactive_captures = 1

        status = self.controller.status()

        self.assertIsNotNone(status["context_error"])
        self.assertEqual([], self.activator.calls)

    def test_cancel_never_executes_and_returns_ready(self):
        preview = self.command('"완료" 입력해줘', "request-cancel")
        confirmation = preview["data"]["confirmation"]

        result = self.parser.resolve_pending_confirmation(
            "chat-stage5",
            confirmation_id=confirmation["confirmation_id"],
            option_id="cancel",
        )

        self.assertFalse(result["success"])
        self.assertEqual("user_cancelled", result["error_type"])
        self.assertEqual(0, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_changed_selection_between_preview_and_approval_is_blocked(self):
        preview = self.command("42 입력해줘", "request-stale")
        confirmation = preview["data"]["confirmation"]
        self.context.address = "B9"
        self.context.context_fingerprint = "E" * 64

        result = self.parser.resolve_pending_confirmation(
            "chat-stage5",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

        self.assertFalse(result["success"])
        self.assertEqual("context_changed", result["status"])
        self.assertEqual(0, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_native_action_for_a_different_document_is_blocked(self):
        self.native.wrong_document = True
        result = self.command("42 입력해줘", "request-wrong-doc")

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual(0, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_read_selection_runs_without_confirmation_or_native_write(self):
        result = self.command("선택 내용 보여줘", "request-read")

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertIn("10", result["message"])
        self.assertEqual(0, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])


if __name__ == "__main__":
    unittest.main()
