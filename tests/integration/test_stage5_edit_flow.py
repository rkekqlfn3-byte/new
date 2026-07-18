import tempfile
import unittest
from pathlib import Path

from engine.app_actions import PreparedAction
from engine.edit_mode import (
    EditContextInactive,
    FileIntakeManager,
    EditModeController,
    EditSessionManager,
    EditSessionStale,
)
from engine.edit_mode.context import EditContextUnavailable
from engine.edit_mode.contracts import EditRequest
from engine.edit_mode.native_bridge import NativeOfficeBusy
from engine.parser import CommandParser


class FakeDocumentBridge:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.window_handle = 10
        self.document_open = True
        self.busy_error = None
        self.find_calls = 0
        self.launches = []
        self.wait_calls = 0
        self.reopen_succeeds = False
        self.replace_after_missing_find = False

    def is_available(self, app_type):
        return True

    def find_document(self, app_type, expected_path=None):
        self.find_calls += 1
        if self.busy_error is not None:
            raise self.busy_error
        if not self.document_open:
            if self.replace_after_missing_find:
                self.replace_after_missing_find = False
                target = Path(self.path)
                target.unlink()
                target.write_bytes(b"replacement-during-rediscovery")
            return None
        return {
            "app_type": app_type,
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": self.window_handle,
            "is_saved": True,
        }

    def launch_document(self, app_type, file_path):
        self.launches.append((app_type, str(file_path)))
        if self.reopen_succeeds:
            self.document_open = True

    def wait_for_document(self, app_type, expected_path, timeout=15.0):
        self.wait_calls += 1
        if not self.document_open:
            return None
        return {
            "app_type": app_type,
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": self.window_handle,
            "is_saved": True,
        }

    def active_documents(self, app_type=None):
        return []


class FakeIntakeManager:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.bridge = FakeDocumentBridge(path)

    def connect_file(self, file_path):
        return {
            "app_type": "excel",
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": 10,
            "active_container": "Sheet1",
            "selection_reference": "A2",
        }

    def reopen_exact_file(self, file_path):
        return FileIntakeManager(
            self.bridge, open_timeout=1
        ).reopen_exact_file(file_path)


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
        self.unavailable_captures = 0
        self.document_fingerprint_override = None
        self.captured_window_handles = []

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        self.captured_window_handles.append(int(value.get("window_handle") or 0))
        if self.unavailable_captures > 0:
            self.unavailable_captures -= 1
            raise EditContextUnavailable(
                "연결된 문서가 닫혔거나 네이티브 앱에서 다시 찾을 수 없습니다."
            )
        if self.inactive_captures > 0:
            self.inactive_captures -= 1
            raise EditContextInactive(
                "연결된 문서가 현재 활성 문서가 아닙니다."
            )
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
        self.intake = FakeIntakeManager(self.file)
        self.bridge = self.intake.bridge
        self.controller = EditModeController(
            intake_manager=self.intake,
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

    def test_closed_document_is_rediscovered_and_window_rebound_once(self):
        self.context.unavailable_captures = 1
        self.bridge.window_handle = 77

        preview = self.command("42 입력해줘", "request-rediscovery")

        self.assertEqual("confirmation_required", preview["status"])
        recovery = preview["data"]["automatic_recovery"]
        self.assertEqual("connected_document_rediscovery", recovery["strategy"])
        self.assertEqual("recovered", recovery["outcome"])
        self.assertEqual("pre_execution", recovery["phase"])
        self.assertFalse(recovery["execution_started"])
        self.assertTrue(recovery["target_unchanged"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertEqual(1, recovery["retry_limit"])
        self.assertEqual(1, self.bridge.find_calls)
        self.assertEqual([], self.bridge.launches)
        self.assertEqual(
            77, self.controller.status()["session"]["window_handle"]
        )
        self.assertEqual(77, self.context.captured_window_handles[-1])
        self.assertEqual(0, self.native.executed)
        self.assertIn("다시 찾아", preview["message"])

    def test_truly_closed_document_reopen_timeout_is_retryable_without_loop(self):
        self.context.unavailable_captures = 1
        self.bridge.document_open = False

        result = self.command("42 입력해줘", "request-rediscovery-missing")

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("connected_document_reopen", recovery["strategy"])
        self.assertEqual("unavailable", recovery["outcome"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertEqual(1, self.bridge.find_calls)
        self.assertEqual(1, len(self.bridge.launches))
        self.assertEqual(1, self.bridge.wait_calls)
        self.assertTrue(result["retryable"])
        self.assertEqual(0, self.native.executed)
        self.assertIn("자동으로 다시 열지 못했습니다", result["message"])

    def test_closed_saved_document_is_reopened_once_before_preview(self):
        self.context.unavailable_captures = 1
        self.bridge.document_open = False
        self.bridge.reopen_succeeds = True
        self.bridge.window_handle = 88

        preview = self.command("42 입력해줘", "request-reopen-success")

        self.assertEqual("confirmation_required", preview["status"])
        recovery = preview["data"]["automatic_recovery"]
        self.assertEqual("connected_document_reopen", recovery["strategy"])
        self.assertEqual("recovered", recovery["outcome"])
        self.assertEqual("pre_execution", recovery["phase"])
        self.assertFalse(recovery["execution_started"])
        self.assertTrue(recovery["target_unchanged"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertEqual(1, recovery["retry_limit"])
        self.assertEqual(1, self.bridge.find_calls)
        self.assertEqual(1, len(self.bridge.launches))
        self.assertEqual(1, self.bridge.wait_calls)
        self.assertEqual([88], self.activator.calls)
        self.assertEqual(88, self.context.captured_window_handles[-1])
        self.assertEqual(0, self.native.executed)
        self.assertIn("같은 저장 파일로 한 번 다시 열고", preview["message"])

    def test_closed_document_is_not_reopened_from_existing_approval(self):
        preview = self.command("42 입력해줘", "request-approval-no-reopen")
        confirmation = preview["data"]["confirmation"]
        self.context.unavailable_captures = 1
        self.bridge.document_open = False
        self.bridge.reopen_succeeds = True

        result = self.parser.resolve_pending_confirmation(
            "chat-stage5",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("connected_document_rediscovery", recovery["strategy"])
        self.assertEqual("not_found", recovery["outcome"])
        self.assertEqual([], self.bridge.launches)
        self.assertEqual(0, self.native.executed)
        self.assertEqual("ready", self.controller.status()["session"]["state"])

        self.context.unavailable_captures = 1
        next_preview = self.command(
            "42 입력해줘", "request-after-stale-approval"
        )
        self.assertEqual("confirmation_required", next_preview["status"])
        self.assertEqual(
            "connected_document_reopen",
            next_preview["data"]["automatic_recovery"]["strategy"],
        )
        self.assertEqual(1, len(self.bridge.launches))

    def test_file_replaced_after_rediscovery_is_not_reopened(self):
        self.context.unavailable_captures = 1
        self.bridge.document_open = False
        self.bridge.reopen_succeeds = True
        self.bridge.replace_after_missing_find = True

        result = self.command("42 입력해줘", "request-reopen-race")

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("connected_document_reopen", recovery["strategy"])
        self.assertEqual("target_changed", recovery["outcome"])
        self.assertFalse(recovery["target_unchanged"])
        self.assertEqual([], self.bridge.launches)
        self.assertEqual(0, self.native.executed)

    def test_busy_rediscovery_is_environment_blocked_and_retryable(self):
        self.context.unavailable_captures = 1
        self.bridge.busy_error = NativeOfficeBusy("Office 응답 대기")

        result = self.command("42 입력해줘", "request-rediscovery-busy")

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("unavailable", recovery["outcome"])
        self.assertTrue(result["retryable"])
        self.assertEqual(0, self.native.executed)

    def test_replaced_document_command_is_blocked_by_session_validation(self):
        self.file.unlink()
        self.file.write_bytes(b"replacement-with-new-identity")

        result = self.command("42 입력해줘", "request-rediscovery-replaced")

        self.assertFalse(result["success"])
        self.assertNotIn("automatic_recovery", result.get("data") or {})
        self.assertEqual(0, self.bridge.find_calls)
        self.assertEqual(0, self.native.executed)
        self.assertIn("다시 연결", result["message"])

    def test_replaced_file_race_is_target_changed_before_any_search(self):
        # A replacement between session validation and context capture must
        # close the recovery as target_changed without touching the bridge.
        self.context.unavailable_captures = 1
        request = EditRequest(
            text="42 입력해줘",
            edit_session_id=self.session["session_id"],
            document_fingerprint=self.session["document_fingerprint"],
        )
        self.file.unlink()
        self.file.write_bytes(b"replacement-with-new-identity")

        with self.assertRaises(EditSessionStale) as raised:
            self.controller._capture_context_for_edit_request(
                dict(self.controller.session_manager.current()), request
            )

        recovery = raised.exception.diagnostic_context["automatic_recovery"]
        self.assertEqual("connected_document_rediscovery", recovery["strategy"])
        self.assertEqual("target_changed", recovery["outcome"])
        self.assertFalse(recovery["target_unchanged"])
        self.assertEqual(0, self.bridge.find_calls)
        self.assertIn("다시 연결", str(raised.exception))

    def test_runtime_document_is_never_rediscovered(self):
        self.context.unavailable_captures = 1
        request = EditRequest(
            text="42 입력해줘",
            edit_session_id=self.session["session_id"],
            document_fingerprint=self.session["document_fingerprint"],
        )
        runtime_session = dict(self.controller.session_manager.current())
        runtime_session["identity_kind"] = "runtime"

        with self.assertRaises(EditContextUnavailable):
            self.controller._capture_context_for_edit_request(
                runtime_session, request, allow_reopen=True
            )
        self.assertEqual(0, self.bridge.find_calls)
        self.assertEqual([], self.bridge.launches)

    def test_status_polling_never_rediscovers_documents(self):
        self.context.unavailable_captures = 1

        status = self.controller.status()

        self.assertIsNotNone(status["context_error"])
        self.assertEqual(0, self.bridge.find_calls)
        self.assertEqual([], self.bridge.launches)

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
