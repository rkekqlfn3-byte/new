"""Regression tests for the common learned-skill execution boundary."""

import os
import tempfile
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.execution_result import success_result
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.skills import (
    RouteSelector,
    SkillConfirmationRequired,
    SkillExecutor,
    SkillPreflightBlocked,
    SkillProfile,
)
from engine.ui_automation import UIAutomationAmbiguousTarget


class SkillExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-skill-executor-")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(
            os.path.join(self.temp_dir.name, "dictionaries.json")
        )
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(self.parser.dict_mgr, self.parser)
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _plan_skill():
        return {
            "state": "active",
            "plan": [{"action": "wait", "seconds": 0.01}],
            "code": "print('legacy fallback')",
            "learning": {"intent": "TEST_PLAN", "slots": []},
        }

    @staticmethod
    def _python_skill(code="import sys\nprint(sys.argv[1])"):
        return {
            "state": "active",
            "plan": [],
            "code": code,
            "learning": {"intent": "TEST_PYTHON", "slots": []},
            "verification_status": "user_confirmed",
        }

    def test_parser_owns_common_skill_executor(self):
        self.assertIsInstance(self.parser.skill_executor, SkillExecutor)

    def test_route_selector_preserves_current_plan_first_behavior(self):
        skill = self._plan_skill()
        selector = RouteSelector()

        selected = selector.select(skill, profile=SkillProfile.from_skill(skill))

        self.assertEqual("action_plan", selected.route)
        self.assertEqual(("action_plan", "python"), selected.available_routes)

    def test_plan_result_contains_common_route_diagnostics(self):
        skill = self._plan_skill()
        executed = success_result(
            "계획 완료",
            action="action_plan",
            verified=True,
            verification_status="verified",
            verification=[{"status": "verified"}],
        )
        with mock.patch.object(
            self.parser.action_executor, "execute_plan", return_value=executed
        ) as run, mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result"
        ) as record:
            result = self.parser.skill_executor.execute(
                "시스템", "계획스킬", skill=skill, slots={"value": "테스트"}
            )

        run.assert_called_once()
        record.assert_called_once_with("시스템", "계획스킬", True)
        diagnostic = result["data"]["skill_execution"]
        self.assertEqual("action_plan", diagnostic["selected_route"])
        self.assertEqual(["action_plan"], diagnostic["attempted_routes"])
        self.assertEqual("passed", diagnostic["verification_status"])
        self.assertTrue(diagnostic["verified"])

    def test_python_result_uses_same_diagnostic_contract(self):
        skill = self._python_skill()
        ran = success_result(
            "Python 완료",
            action="python_macro",
            verified=False,
            verification_status="confirmation_required",
        )
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=ran
        ) as run, mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result"
        ) as record:
            result = self.parser.skill_executor.execute(
                "시스템", "파이썬스킬", skill=skill, argument="값"
            )

        run.assert_called_once_with(skill["code"], "값")
        record.assert_called_once_with("시스템", "파이썬스킬", True)
        diagnostic = result["data"]["skill_execution"]
        self.assertEqual("python", diagnostic["selected_route"])
        self.assertEqual("safe", diagnostic["preflight_status"])
        self.assertEqual(0, diagnostic["fallback_attempts"])

    def test_confirmation_and_blocking_happen_before_python_runner(self):
        confirmation_skill = self._python_skill(
            "import win32api\nwin32api.MessageBox(0, 'x', 'x', 0)"
        )
        blocked_skill = self._python_skill(
            "import winreg\nwinreg.SetValueEx(None, 'x', 0, 1, 'y')"
        )
        with mock.patch.object(self.parser.macro_runner, "run") as run, \
             mock.patch.object(
                 self.parser.dict_mgr, "record_learned_macro_result"
             ) as record:
            with self.assertRaises(SkillConfirmationRequired):
                self.parser.skill_executor.execute(
                    "시스템", "확인스킬", skill=confirmation_skill
                )
            with self.assertRaises(SkillPreflightBlocked):
                self.parser.skill_executor.execute(
                    "시스템", "차단스킬", skill=blocked_skill
                )

        run.assert_not_called()
        record.assert_not_called()

    def test_runtime_failure_is_recorded_once(self):
        skill = self._python_skill()
        with mock.patch.object(
            self.parser.macro_runner, "run", side_effect=RuntimeError("실패")
        ), mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result"
        ) as record, mock.patch.object(
            self.parser.candidate_recording_service, "record_failure"
        ) as candidate_failure:
            with self.assertRaises(RuntimeError):
                self.parser.skill_executor.execute(
                    "시스템", "실패스킬", skill=skill
                )

        record.assert_called_once_with(
            "시스템", "실패스킬", False, "execution_error"
        )
        candidate_failure.assert_called_once()

    def test_ambiguous_uia_waits_for_choice_without_failure_or_fallback(self):
        skill = self._plan_skill()
        ambiguity = UIAutomationAmbiguousTarget(
            "후보가 두 개입니다.", candidates=[]
        )
        ambiguity.failed_step = 1
        with mock.patch.object(
            self.parser.action_executor,
            "execute_plan",
            side_effect=ambiguity,
        ), mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result"
        ) as record:
            with self.assertRaises(UIAutomationAmbiguousTarget) as raised:
                self.parser.skill_executor.execute(
                    "시스템", "모호한스킬", skill=skill
                )

        record.assert_not_called()
        diagnostic = raised.exception.skill_execution
        self.assertEqual("action_plan", diagnostic["selected_route"])
        self.assertFalse(diagnostic["fallback_used"])


class SkillExecutorEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-skill-entry-")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(
            os.path.join(self.temp_dir.name, "dictionaries.json")
        )
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(self.parser.dict_mgr, self.parser)
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict
        self.completed = success_result(
            "완료",
            action="action_plan",
            verified=True,
            verification_status="verified",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _install(self, *, code="", plan=None):
        learned = {
            "state": "active",
            "code": code,
            "plan": list(plan or []),
            "learning": {"intent": "ENTRY_TEST", "slots": []},
            "run_policy": "auto",
            "run_policy_history": [],
            "verified_success_count": 5,
            "consecutive_verified_success": 5,
        }
        self.parser.dict_mgr.learned_macros = {
            "시스템": {"공통실행": learned}
        }
        self.parser.dict_mgr.macro_dict = {
            "공통실행": {
                "name": "공통실행",
                "type": "learned",
                "app": "시스템",
                "synonyms": ["공통실행"],
            }
        }
        return learned

    def test_local_match_delegates_to_skill_executor(self):
        learned = self._install(plan=[{"action": "wait", "seconds": 0.01}])
        with mock.patch.object(
            self.parser.skill_executor, "execute", return_value=self.completed
        ) as execute:
            result = self.parser.execute_command_result("공통실행")

        self.assertTrue(result["success"])
        self.assertIs(learned, execute.call_args.kwargs["skill"])
        self.assertEqual("local_match", execute.call_args.kwargs["source"])

    def test_ai_learned_and_adapted_actions_delegate_to_same_executor(self):
        learned = self._install(code="import sys\nprint(sys.argv[1])")
        actions = [
            {
                "action": "use_learned_macro",
                "app_name": "시스템",
                "macro_name": "공통실행",
                "target": "기본값",
            },
            {
                "action": "adapted_macro",
                "app_name": "시스템",
                "macro_name": "공통실행",
                "target": "응용값",
                "code": "import sys\nprint(sys.argv[1].upper())",
            },
        ]
        with mock.patch.object(
            self.parser.skill_executor, "execute", return_value=self.completed
        ) as execute:
            waiting = self.parser.ai_action_handler.execute_batch(
                "실행합니다.", actions, [], "공통 실행 테스트",
                session_id="skill-entry-policy",
            )
            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            result = self.parser.resolve_pending_confirmation(
                "skill-entry-policy", confirmation_id, "run_once"
            )

        self.assertTrue(result["success"])
        self.assertEqual(2, execute.call_count)
        self.assertTrue(all(
            call.kwargs["skill"] is learned for call in execute.call_args_list
        ))
        self.assertEqual(
            ["ai_learned", "adapted_macro"],
            [call.kwargs["source"] for call in execute.call_args_list],
        )
        self.assertNotIn("code_override", execute.call_args_list[0].kwargs)
        self.assertIn("code_override", execute.call_args_list[1].kwargs)


if __name__ == "__main__":
    unittest.main()
