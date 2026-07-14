import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine.execution_result import (
    ERROR_TYPES, failure_result, normalize_execution_result, success_result,
)
from engine.execution_runtime import ExecutionController
from engine.api import command_api
from engine.macro_runner import build_macro_command
from engine.macro_worker import MACRO_WORKER_FLAG, run_macro_worker
from engine.parser import CommandParser


class ExecutionResultContractTests(unittest.TestCase):
    def test_success_and_failure_have_the_same_contract(self):
        success = success_result("완료", action="test", verified=True)
        failure = failure_result(
            "실패", action="test", error_type="target_not_found",
            failed_step=2, retryable=True,
        )
        required = {
            "success", "message", "action", "target", "verified", "status",
            "error_type", "failed_step", "retryable", "data",
        }
        self.assertTrue(required.issubset(success))
        self.assertTrue(required.issubset(failure))
        self.assertTrue(success["success"])
        self.assertFalse(failure["success"])
        self.assertEqual("target_not_found", failure["error_type"])

    def test_legacy_text_is_only_normalized_at_compatibility_boundary(self):
        result = normalize_execution_result("외부 플러그인 응답")
        self.assertTrue(result["success"])
        self.assertFalse(result["verified"])
        self.assertEqual("외부 플러그인 응답", result["message"])

    def test_success_without_postcondition_is_not_verified_by_default(self):
        self.assertFalse(success_result("실행 완료")["verified"])
        self.assertFalse(normalize_execution_result({
            "success": True, "message": "외부 실행 완료"
        })["verified"])

    def test_learned_plan_normalizes_manual_confirmation_required(self):
        parser = CommandParser()
        parser.dict_mgr.learned_macros = {"시스템": {"검증테스트": {
            "state": "active",
            "plan": [{"action": "type_text", "text": "테스트"}],
            "learning": {"utterances": ["검증 테스트 실행"]},
        }}}
        parser.dict_mgr.macro_dict["검증테스트"] = {
            "type": "learned", "app": "시스템",
            "synonyms": ["검증 테스트 실행"],
        }
        execution = success_result(
            "행동 계획 실행", action="action_plan", verified=False,
            verification_status="confirmation_required",
        )
        with (
            patch.object(
                parser.action_executor, "execute_plan", return_value=execution
            ),
            patch.object(parser.dict_mgr, "record_learned_macro_result"),
        ):
            waiting = parser.execute_command_result(
                "검증 테스트 실행", session_id="result-policy"
            )
            self.assertEqual("confirmation_required", waiting["status"])
            result = parser.resolve_pending_confirmation(
                "result-policy",
                waiting["data"]["confirmation"]["confirmation_id"],
                "run_once",
            )
        self.assertTrue(result["success"])
        self.assertFalse(result["verified"])
        self.assertEqual(
            "manual_confirmation_required", result["verification_status"]
        )
        self.assertFalse(result["data"]["execution_result"]["verified"])

    def test_parser_returns_object_internally_and_message_publicly(self):
        parser = CommandParser()
        parser.dict_mgr.noun_dict = {"메모장": "notepad.exe"}
        parser.dict_mgr.noun_revision += 1
        with patch("engine.builtins.os.startfile", side_effect=OSError("blocked")):
            result = parser.execute_command_result("메모장 열어")
            message = parser.parse_and_execute("메모장 열어")
        self.assertFalse(result["success"])
        self.assertEqual("execution_error", result["error_type"])
        self.assertIn("에러", result["message"])
        self.assertIsInstance(message, str)

    def test_error_taxonomy_is_closed(self):
        self.assertEqual({
            "validation_error", "verification_error", "execution_error",
            "timeout", "target_not_found", "user_cancelled",
            "environment_error", "unknown",
        }, set(ERROR_TYPES))

    def test_unhandled_exception_is_returned_as_structured_failure(self):
        parser = CommandParser()
        with patch.object(
            parser, "_parse_and_execute_core",
            side_effect=FileNotFoundError("missing"),
        ):
            result = parser.execute_command_result("테스트")
        self.assertFalse(result["success"])
        self.assertEqual("target_not_found", result["error_type"])

    def test_command_api_and_diagnostics_keep_failure_status(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-result-test-") as temp_dir:
            parser = CommandParser()
            parser.dict_mgr.noun_dict = {"메모장": "notepad.exe"}
            parser.dict_mgr.noun_revision += 1
            controller = ExecutionController(
                os.path.join(temp_dir, "diagnostics.json")
            )
            parser.execution_controller = controller
            parser.action_executor.controller = controller
            parser.macro_runner.controller = controller
            with (
                patch.object(command_api, "parser", parser),
                patch("engine.builtins.os.startfile", side_effect=OSError("blocked")),
            ):
                result = command_api.parse_command("메모장 열어")
            record = controller.diagnostics(1)["records"][-1]
            self.assertFalse(result["success"])
            self.assertFalse(record["success"])
            self.assertEqual("execution_error", record["error_type"])
            self.assertEqual(result["message"], record["response"])

    def test_cancelled_command_recovers_for_the_next_command(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-cancel-test-") as temp_dir:
            parser = CommandParser()
            controller = ExecutionController(
                os.path.join(temp_dir, "diagnostics.json")
            )
            parser.execution_controller = controller
            parser.action_executor.controller = controller
            parser.macro_runner.controller = controller
            results = []

            def long_command(*args, **kwargs):
                controller.wait(5)
                return success_result("끝", verified=True)

            with (
                patch.object(command_api, "parser", parser),
                patch.object(parser, "_parse_and_execute_core", side_effect=long_command),
            ):
                worker = threading.Thread(
                    target=lambda: results.append(command_api.parse_command("긴 명령"))
                )
                worker.start()
                deadline = time.monotonic() + 2
                while not controller.current and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(command_api.cancel_current_execution()["success"])
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(results[0]["success"])
            self.assertEqual("user_cancelled", results[0]["error_type"])

            with (
                patch.object(command_api, "parser", parser),
                patch.object(
                    parser, "_parse_and_execute_core",
                    return_value=success_result("다음 명령 성공", verified=True),
                ),
            ):
                next_result = command_api.parse_command("다음 명령")
            self.assertTrue(next_result["success"])
            records = controller.diagnostics(2)["records"]
            self.assertEqual(["cancelled", "success"], [r["status"] for r in records])
            self.assertEqual("user_cancelled", records[0]["error_type"])
            self.assertTrue(records[1]["success"])


class FrozenMacroWorkerTests(unittest.TestCase):
    def test_source_and_frozen_commands_are_different(self):
        source = build_macro_command("macro.py", "arg", "python.exe", frozen=False)
        frozen = build_macro_command("macro.py", "arg", "Jarvis.exe", frozen=True)
        self.assertEqual(["python.exe", "macro.py", "arg"], source)
        self.assertEqual(
            ["Jarvis.exe", MACRO_WORKER_FLAG, "macro.py", "arg"], frozen
        )

    def test_worker_executes_script_with_argument(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-worker-test-") as temp_dir:
            script = os.path.join(temp_dir, "macro.py")
            output = os.path.join(temp_dir, "result.txt")
            with open(script, "w", encoding="utf-8") as file:
                file.write(
                    "import pathlib, sys\n"
                    "pathlib.Path(sys.argv[1]).write_text('worker-ok', encoding='utf-8')\n"
                )
            code = run_macro_worker([MACRO_WORKER_FLAG, script, output])
            self.assertEqual(0, code)
            with open(output, encoding="utf-8") as file:
                self.assertEqual("worker-ok", file.read())


if __name__ == "__main__":
    unittest.main()
