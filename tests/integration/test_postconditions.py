"""Stage 8 tests for common postconditions and verification aggregation."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.execution_result import success_result
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.skills import (
    MANUAL_CONFIRMATION_REQUIRED,
    NOT_AVAILABLE,
    NOT_REQUIRED,
    PASSED,
    PostconditionEvaluator,
    SkillVerificationFailed,
)


def _success(**extra):
    return success_result("완료", action="action_plan", **extra)


class PostconditionEvaluatorTests(unittest.TestCase):
    def test_process_success_and_verified_success_are_separate(self):
        summary = PostconditionEvaluator().evaluate(
            _success(
                verified=True,
                verification_status="verified",
                verification=[{"status": "verified"}],
            ),
            route="action_plan",
        )

        self.assertTrue(summary.process_success)
        self.assertTrue(summary.verified)
        self.assertEqual(PASSED, summary.verification_status)
        self.assertTrue(summary.result["process_success"])
        self.assertTrue(summary.result["verified_success"])

    def test_no_method_and_manual_confirmation_are_distinct(self):
        not_required = PostconditionEvaluator().evaluate(
            _success(), route="action_plan"
        )
        manual = PostconditionEvaluator().evaluate(
            _success(), route="uia"
        )

        self.assertEqual(NOT_REQUIRED, not_required.verification_status)
        self.assertTrue(not_required.result["success"])
        self.assertFalse(not_required.verified)
        self.assertEqual(
            MANUAL_CONFIRMATION_REQUIRED, manual.verification_status
        )
        self.assertFalse(manual.verified)

    def test_multiple_file_and_text_conditions_all_pass(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-postcondition-") as root:
            path = os.path.join(root, "result.txt")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("JARVIS stage 8 complete")
            summary = PostconditionEvaluator().evaluate(
                _success(),
                route="action_plan",
                slots={"path": path, "needle": "stage 8"},
                conditions=[
                    {
                        "type": "file_exists",
                        "target": {"source": "slot", "name": "path"},
                    },
                    {
                        "type": "file_created",
                        "target": {"path": "{path}"},
                    },
                    {
                        "type": "text_contains",
                        "target": {"path": "{path}"},
                        "expected": {"source": "slot", "name": "needle"},
                    },
                ],
            )

        self.assertEqual(PASSED, summary.verification_status)
        self.assertEqual(3, len(summary.checks))
        self.assertTrue(all(item["passed"] for item in summary.checks))

    def test_one_failed_condition_makes_final_result_a_real_failure(self):
        summary = PostconditionEvaluator().evaluate(
            _success(),
            route="action_plan",
            conditions=[
                {
                    "type": "text_contains",
                    "target": {"text": "actual"},
                    "expected": "actual",
                },
                {
                    "type": "text_contains",
                    "target": {"text": "actual"},
                    "expected": "missing",
                },
            ],
        )

        self.assertTrue(summary.process_success)
        self.assertFalse(summary.verified)
        self.assertEqual("failed", summary.verification_status)
        self.assertFalse(summary.result["success"])
        self.assertEqual("verification_failed", summary.result["status"])
        self.assertEqual("verification_error", summary.result["error_type"])

    def test_existing_excel_adapter_evidence_is_reused_for_value_and_format(self):
        excel_result = _success(
            data={
                "step_results": [{
                    "output": {
                        "success": True,
                        "verified": True,
                        "app": "excel",
                        "workbook_name": "Book1.xlsx",
                        "sheet": "Sheet1",
                        "target": "B1",
                        "verification_method": "read_cell_value",
                        "after": {
                            "current_value": 42,
                            "desired": {"bold": True, "font_size": 12},
                        },
                    }
                }]
            },
        )
        summary = PostconditionEvaluator().evaluate(
            excel_result,
            route="action_plan",
            slots={"result": 42},
            conditions=[
                {
                    "type": "cell_equals",
                    "target": {
                        "workbook": "Book1.xlsx",
                        "sheet": "Sheet1",
                        "cell": "B1",
                    },
                    "expected": {"source": "slot", "name": "result"},
                },
                {
                    "type": "cell_format_matches",
                    "target": {
                        "workbook": "Book1.xlsx",
                        "sheet": "Sheet1",
                        "cell": "B1",
                    },
                    "expected": {"bold": True},
                },
            ],
        )

        self.assertEqual(PASSED, summary.verification_status)
        self.assertEqual(
            ["read_cell_value", "read_cell_value"],
            [item["verification_method"] for item in summary.checks],
        )

    def test_window_and_control_conditions_use_existing_uia_adapter(self):
        window = mock.Mock()
        window.window_text.return_value = "메모장"
        control = mock.Mock()
        control.window_text.return_value = "저장"
        uia = mock.Mock()
        uia.find_window.return_value = window
        uia._find_control.return_value = control
        owner = SimpleNamespace(
            action_executor=SimpleNamespace(ui_automation=uia)
        )
        summary = PostconditionEvaluator(owner.action_executor).evaluate(
            _success(),
            route="uia",
            conditions=[
                {"type": "window_exists", "target": {"app": "메모장"}},
                {
                    "type": "control_exists",
                    "target": {"app": "메모장", "control": "저장"},
                },
            ],
        )

        self.assertEqual(PASSED, summary.verification_status)
        uia.find_window.assert_called_with("메모장")
        uia._find_control.assert_called_once_with(
            window, "저장", editable=False
        )

    def test_unavailable_checker_is_not_invented_as_success(self):
        summary = PostconditionEvaluator().evaluate(
            _success(),
            route="action_plan",
            conditions=[{
                "type": "cell_equals",
                "target": {"sheet": "Sheet1", "cell": "A1"},
                "expected": 1,
            }],
        )

        self.assertEqual(NOT_AVAILABLE, summary.verification_status)
        self.assertTrue(summary.result["success"])
        self.assertFalse(summary.verified)


class SkillPostconditionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-stage8-")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(
            os.path.join(self.temp_dir.name, "dictionaries.json")
        )
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(
            self.parser.dict_mgr, self.parser.action_executor
        )
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict
        self.executor = self.parser.skill_executor

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _failing_skill(**extra):
        skill = {
            "state": "active",
            "plan": [{"action": "wait", "seconds": 0.01}],
            "postconditions": [{
                "type": "text_contains",
                "target": {"text": "actual"},
                "expected": "missing",
            }],
            "learning": {"intent": "POSTCONDITION_FAILURE", "slots": []},
        }
        skill.update(extra)
        return skill

    def test_verification_failure_never_uses_fallback(self):
        skill = self._failing_skill(
            uia_plan=[{"action": "wait", "seconds": 0.01}],
            execution_profile={
                "primary_route": "action_plan",
                "fallback_routes": ["uia"],
                "verification_required": True,
                "max_fallback_attempts": 1,
            },
        )
        with mock.patch.object(
            self.parser.action_executor,
            "execute_plan",
            return_value=_success(),
        ) as run:
            with self.assertRaises(SkillVerificationFailed) as raised:
                self.executor.execute("시스템", "검증실패", skill=skill)

        run.assert_called_once()
        self.assertEqual("verification_failed", raised.exception.status)
        self.assertFalse(raised.exception.result["success"])

    def test_verification_failure_does_not_increment_success_metrics(self):
        skill = self._failing_skill(
            plan=[],
            code="print('done')",
            verification_status="user_confirmed",
        )
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=_success()
        ), mock.patch.object(
            self.parser.candidate_recording_service, "record_success"
        ) as candidate_success, mock.patch.object(
            self.parser.candidate_recording_service, "record_failure"
        ) as candidate_failure, mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result"
        ) as usage:
            with self.assertRaises(SkillVerificationFailed):
                self.executor.execute("시스템", "지표실패", skill=skill)

        candidate_success.assert_not_called()
        candidate_failure.assert_called_once()
        usage.assert_called_once_with(
            "시스템", "지표실패", False, "verification_error"
        )

    def test_legacy_skill_without_postconditions_still_runs(self):
        skill = {
            "state": "active",
            "plan": [{"action": "wait", "seconds": 0.01}],
            "learning": {"intent": "LEGACY", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor,
            "execute_plan",
            return_value=_success(
                verified=True,
                verification_status="verified",
                verification=[{"status": "verified"}],
            ),
        ):
            result = self.executor.execute("시스템", "기존", skill=skill)

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(PASSED, result["verification_status"])

    def test_parser_reports_postcondition_failure_to_the_user(self):
        skill = self._failing_skill()
        self.parser.dict_mgr.learned_macros = {
            "시스템": {"사후조건실패": skill}
        }
        self.parser.dict_mgr.macro_dict = {
            "사후조건실패": {
                "name": "사후조건실패",
                "type": "learned",
                "app": "시스템",
                "synonyms": ["사후조건 실패 실행"],
            }
        }
        with mock.patch.object(
            self.parser.action_executor,
            "execute_plan",
            return_value=_success(),
        ):
            waiting = self.parser.execute_command_result(
                "사후조건 실패 실행", session_id="postcondition-policy"
            )
            self.assertEqual("confirmation_required", waiting["status"])
            result = self.parser.resolve_pending_confirmation(
                "postcondition-policy",
                waiting["data"]["confirmation"]["confirmation_id"],
                "run_once",
            )

        self.assertFalse(result["success"])
        self.assertEqual("verification_failed", result["status"])
        self.assertEqual("verification_error", result["error_type"])
        self.assertIn("사후조건 검증에 실패", result["message"])


if __name__ == "__main__":
    unittest.main()
