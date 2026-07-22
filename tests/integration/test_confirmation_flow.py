import os
import tempfile
import unittest
from unittest import mock

from engine.api import command_api
from engine.confirmation import ConfirmationResponseHandler
from engine.execution_result import confirmation_result
from engine.execution_runtime import ExecutionBusyError, ExecutionController
from engine.managers.pending_confirmation_manager import (
    ConfirmationAlreadyConsumedError,
    ConfirmationConflictError,
    ConfirmationExpiredError,
    ConfirmationSessionMismatchError,
    PendingConfirmationError,
    PendingConfirmationManager,
)
from engine.parser import CommandParser


def _options():
    return [
        {
            "id": "continue",
            "label": "계속",
            "aliases": ["네", "응"],
        },
        {
            "id": "cancel",
            "label": "취소",
            "cancel": True,
        },
    ]


class PendingConfirmationManagerTests(unittest.TestCase):
    def test_clarification_kind_requires_a_closed_reason_code(self):
        manager = PendingConfirmationManager()
        record = manager.create(
            session_id="clarify",
            execution_id="run-clarify",
            original_command="이거 합계 내줘",
            reason="missing_range",
            request_kind="clarification",
            message="어느 범위를 합계할까요?",
            options=[{"id": "selection", "label": "현재 선택"}],
        )
        self.assertEqual("clarification", record["request_kind"])
        with self.assertRaises(PendingConfirmationError):
            PendingConfirmationManager().create(
                session_id="bad-clarify",
                execution_id="run-bad",
                original_command="모호한 요청",
                reason="free_form_reason",
                request_kind="clarification",
                message="무엇을 할까요?",
                options=[{"id": "one", "label": "하나"}],
            )
    def test_session_has_only_one_active_request_and_payload_is_private(self):
        manager = PendingConfirmationManager()
        record = manager.create(
            session_id="session-a",
            execution_id="exec-a",
            original_command="테스트",
            reason="multiple_valid_methods",
            message="선택해주세요.",
            options=_options(),
            payload={"secret": "internal-plan"},
        )
        with self.assertRaises(ConfirmationConflictError):
            manager.create(
                session_id="session-a",
                execution_id="exec-b",
                original_command="두 번째",
                reason="ambiguous_scope",
                message="다시 선택해주세요.",
                options=_options(),
            )
        public = manager.active_record("session-a", public=True)
        self.assertEqual(record["confirmation_id"], public["confirmation_id"])
        self.assertNotIn("payload", public)
        self.assertNotIn("aliases", public["options"][0])

    def test_text_resolution_consumes_once_and_rejects_other_session(self):
        manager = PendingConfirmationManager()
        record = manager.create(
            session_id="session-a",
            execution_id="exec-a",
            original_command="테스트",
            reason="confirmation_demo",
            message="계속할까요?",
            options=_options(),
        )
        self.assertEqual("continue", manager.resolve_text("session-a", "  네 "))
        with self.assertRaises(ConfirmationSessionMismatchError):
            manager.consume("session-b", record["confirmation_id"], "continue")
        consumed = manager.consume(
            "session-a", record["confirmation_id"], "continue"
        )
        self.assertEqual("consumed", consumed["status"])
        with self.assertRaises(ConfirmationAlreadyConsumedError):
            manager.consume("session-a", record["confirmation_id"], "continue")

    def test_expired_request_is_removed_and_reports_execution_for_cleanup(self):
        now = [1_700_000_000.0]
        manager = PendingConfirmationManager(
            ttl_seconds=5, clock=lambda: now[0]
        )
        record = manager.create(
            session_id="session-a",
            execution_id="exec-a",
            original_command="테스트",
            reason="confirmation_demo",
            message="계속할까요?",
            options=_options(),
        )
        now[0] += 6
        self.assertIsNone(manager.active_record("session-a"))
        self.assertEqual(["exec-a"], manager.drain_expired_execution_ids())
        with self.assertRaises(ConfirmationExpiredError):
            manager.consume("session-a", record["confirmation_id"], "continue")


class ConfirmationResultTests(unittest.TestCase):
    def test_confirmation_is_non_terminal_and_has_no_error_type(self):
        result = confirmation_result(
            "선택해주세요.", {"confirmation_id": "confirm_test"}
        )
        self.assertFalse(result["success"])
        self.assertFalse(result["verified"])
        self.assertEqual("confirmation_required", result["status"])
        self.assertIsNone(result["error_type"])
        self.assertEqual(
            "confirm_test", result["data"]["confirmation"]["confirmation_id"]
        )


class ConfirmationExecutionRuntimeTests(unittest.TestCase):
    def test_paused_execution_is_not_completed_and_blocks_other_work(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-runtime-") as temp_dir:
            path = os.path.join(temp_dir, "diagnostics.json")
            controller = ExecutionController(path)
            first_id = controller.begin("first", {"session_id": "a"})
            controller.pause_for_confirmation("confirm-a")
            diagnostics = controller.diagnostics()
            self.assertIsNone(diagnostics["current"])
            self.assertEqual([], diagnostics["records"])
            self.assertEqual(first_id, diagnostics["pending"][0]["execution_id"])

            with self.assertRaises(ExecutionBusyError):
                controller.begin("second", {"session_id": "b"})
            self.assertTrue(controller.resume(first_id))
            first_record = controller.finish(True, response="first done")
            reloaded = ExecutionController(path)

        self.assertEqual(first_id, first_record["execution_id"])
        self.assertEqual([first_id], [record["execution_id"] for record in reloaded.records])
        self.assertEqual({}, reloaded.pending)


class ConfirmationResponseHandlerTests(unittest.TestCase):
    class Owner:
        def __init__(self, manager, controller):
            self.pending_confirmation_manager = manager
            self.execution_controller = controller

    def test_text_response_consumes_once_before_domain_execution(self):
        manager = PendingConfirmationManager()
        controller = mock.Mock()
        record = manager.create(
            session_id="handler-session",
            execution_id="handler-execution",
            original_command="테스트",
            reason="confirmation_demo",
            message="계속할까요?",
            options=_options(),
            payload={"kind": "demo"},
        )
        handler = ConfirmationResponseHandler(self.Owner(manager, controller))

        first = handler.resolve_selection("handler-session", user_text="네")
        duplicate = handler.resolve_selection(
            "handler-session",
            confirmation_id=record["confirmation_id"],
            option_id="continue",
        )

        self.assertIsNone(first.result)
        self.assertEqual("continue", first.option_id)
        self.assertEqual("consumed", first.consumed["status"])
        controller.resume.assert_called_once_with("handler-execution")
        self.assertEqual("confirmation_already_consumed", duplicate.result["status"])

    def test_unknown_text_keeps_request_pending_and_cancel_is_terminal(self):
        manager = PendingConfirmationManager()
        controller = mock.Mock()
        record = manager.create(
            session_id="handler-session",
            execution_id="",
            original_command="테스트",
            reason="confirmation_demo",
            message="계속할까요?",
            options=_options(),
        )
        handler = ConfirmationResponseHandler(self.Owner(manager, controller))

        waiting = handler.resolve_selection(
            "handler-session", user_text="무슨 말인지 모르겠어"
        )
        cancelled = handler.resolve_selection(
            "handler-session",
            confirmation_id=record["confirmation_id"],
            option_id="cancel",
        )

        self.assertEqual("confirmation_required", waiting.result["status"])
        self.assertEqual("user_cancelled", cancelled.result["error_type"])
        self.assertIsNone(manager.active_record("handler-session"))

    def test_resume_conflict_does_not_consume_confirmation(self):
        manager = PendingConfirmationManager()
        controller = mock.Mock()
        controller.can_resume.return_value = False
        record = manager.create(
            session_id="handler-session",
            execution_id="handler-execution",
            original_command="test",
            reason="confirmation_demo",
            message="continue?",
            options=_options(),
        )
        handler = ConfirmationResponseHandler(self.Owner(manager, controller))

        result = handler.resolve_selection(
            "handler-session", confirmation_id=record["confirmation_id"], option_id="continue"
        )

        self.assertEqual("state_conflict", result.result["status"])
        self.assertEqual("pending", manager.get_record(record["confirmation_id"])["status"])
        controller.resume.assert_not_called()

    def test_context_fingerprint_is_rechecked_before_resume(self):
        previous = mock.Mock(context_fingerprint="book-a:sheet-a:revision-1")
        same = mock.Mock(context_fingerprint="book-a:sheet-a:revision-1")
        changed = mock.Mock(context_fingerprint="book-a:sheet-a:revision-2")

        self.assertFalse(
            ConfirmationResponseHandler.context_changed(previous, same)
        )
        self.assertTrue(
            ConfirmationResponseHandler.context_changed(previous, changed)
        )


class ConfirmationApiFlowTests(unittest.TestCase):
    @staticmethod
    def _parser(temp_dir):
        parser = CommandParser()
        parser.execution_controller = ExecutionController(
            os.path.join(temp_dir, "diagnostics.json")
        )
        parser.action_executor.controller = parser.execution_controller
        parser.macro_runner.controller = parser.execution_controller
        return parser

    def test_demo_confirmation_text_and_button_flow_are_one_shot(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-api-") as temp_dir:
            parser = self._parser(temp_dir)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "확인 카드 테스트", session_id="session-a"
                )
                confirmation_id = first["data"]["confirmation"]["confirmation_id"]
                waiting = command_api.parse_command(
                    "전혀 다른 명령", session_id="session-a"
                )
                completed = command_api.resolve_confirmation(
                    confirmation_id, "continue", "session-a"
                )
                duplicate = command_api.resolve_confirmation(
                    confirmation_id, "continue", "session-a"
                )

            self.assertEqual("confirmation_required", first["status"])
            self.assertEqual(confirmation_id, waiting["data"]["confirmation"]["confirmation_id"])
            self.assertTrue(completed["success"])
            self.assertEqual("confirmation_demo", completed["action"])
            self.assertFalse(duplicate["success"])
            self.assertEqual("confirmation_already_consumed", duplicate["status"])
            records = parser.execution_controller.diagnostics()["records"]
            self.assertEqual(1, len(records))
            self.assertEqual("success", records[0]["status"])

    def test_other_session_command_is_busy_while_confirmation_is_pending(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-api-") as temp_dir:
            parser = self._parser(temp_dir)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "확인 카드 테스트", session_id="session-a"
                )
                blocked = command_api.parse_command(
                    "a separate command", session_id="session-b"
                )
                confirmation_id = first["data"]["confirmation"]["confirmation_id"]
                completed = command_api.resolve_confirmation(
                    confirmation_id, "continue", "session-a"
                )

        self.assertEqual("confirmation_required", first["status"])
        self.assertEqual("busy", blocked["status"])
        self.assertTrue(completed["success"])

    def test_demo_cancel_is_terminal_without_external_change(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-api-") as temp_dir:
            parser = self._parser(temp_dir)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "확인 카드 테스트", session_id="session-cancel"
                )
                confirmation_id = first["data"]["confirmation"]["confirmation_id"]
                result = command_api.resolve_confirmation(
                    confirmation_id, "cancel", "session-cancel"
                )

            self.assertFalse(result["success"])
            self.assertEqual("cancelled", result["status"])
            self.assertEqual("user_cancelled", result["error_type"])
            self.assertEqual(
                "cancelled",
                parser.execution_controller.diagnostics()["records"][-1]["status"],
            )

    def test_existing_file_changes_only_after_explicit_overwrite_choice(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-file-") as temp_dir:
            destination = os.path.join(temp_dir, "existing.txt")
            with open(destination, "w", encoding="utf-8") as file:
                file.write("기존")
            parser = self._parser(temp_dir)
            parser.llm_engine.process_command = mock.Mock(return_value={
                "response": "파일을 저장합니다.",
                "actions": [{
                    "action": "action_plan",
                    "target": destination,
                    "app_name": "시스템",
                    "macro_name": "confirm_write",
                    "description": "파일 저장",
                    "learning": {
                        "intent": "WRITE_FILE",
                        "verbs": ["저장"],
                        "utterances": ["확인 파일 저장"],
                        "slots": [],
                    },
                    "plan": [{
                        "action": "write_text_file",
                        "target": destination,
                        "text": "새 값",
                        "overwrite": False,
                    }],
                }],
            })
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "확인 파일 저장", session_id="session-file"
                )
                with open(destination, encoding="utf-8") as file:
                    before = file.read()
                confirmation_id = first["data"]["confirmation"]["confirmation_id"]
                completed = command_api.resolve_confirmation(
                    confirmation_id, "overwrite", "session-file"
                )
                duplicate = command_api.resolve_confirmation(
                    confirmation_id, "overwrite", "session-file"
                )
                with open(destination, encoding="utf-8") as file:
                    after = file.read()

            self.assertEqual("기존", before)
            self.assertEqual("confirmation_required", first["status"])
            self.assertTrue(completed["success"])
            self.assertEqual("새 값", after)
            self.assertEqual("confirmation_already_consumed", duplicate["status"])
            self.assertEqual(1, len(parser.pending_macros))


if __name__ == "__main__":
    unittest.main()
