"""Stage 7 regression tests: bounded, safe multi-route fallback."""

import os
import tempfile
import unittest
from unittest import mock

from engine.action_executor import ActionConfirmationRequired
from engine.builtins import BuiltinMacros
from engine.execution_result import confirmation_result, failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.skills import RouteSelector, SkillProfile


def _verified(action="action_plan", message="완료"):
    return success_result(
        message,
        action=action,
        verified=True,
        verification_status="verified",
        verification=[{"status": "verified"}],
    )


class RouteSelectorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.selector = RouteSelector()

    def test_legacy_skill_without_profile_has_no_fallback(self):
        skill = {"plan": [{"action": "wait", "seconds": 0.01}], "code": "print(1)"}
        selection = self.selector.select(
            skill, profile=SkillProfile.from_skill(skill)
        )
        self.assertEqual("action_plan", selection.route)
        self.assertEqual((), selection.fallback_routes)
        self.assertEqual("primary_route_available", selection.reason)

    def test_unavailable_primary_yields_available_fallback_chain(self):
        skill = {
            "uia_plan": [{"action": "wait", "seconds": 0.01}],
            "execution_profile": {
                "primary_route": "action_plan",
                "fallback_routes": ["uia"],
                "max_fallback_attempts": 1,
            },
        }
        selection = self.selector.select(
            skill, profile=SkillProfile.from_skill(skill)
        )
        self.assertEqual("action_plan", selection.route)
        self.assertEqual(("uia",), selection.fallback_routes)
        self.assertEqual("primary_route_unavailable", selection.reason)

    def test_fallback_chain_is_capped_at_one(self):
        skill = {
            "plan": [{"action": "wait", "seconds": 0.01}],
            "uia_plan": [{"action": "wait", "seconds": 0.01}],
            "native_plan": [{"action": "wait", "seconds": 0.01}],
            "execution_profile": {
                "primary_route": "action_plan",
                "fallback_routes": ["uia", "native"],
                "max_fallback_attempts": 5,
            },
        }
        selection = self.selector.select(
            skill, profile=SkillProfile.from_skill(skill)
        )
        self.assertEqual(("uia",), selection.fallback_routes)


class SkillExecutorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-fallback-")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(
            os.path.join(self.temp_dir.name, "dictionaries.json")
        )
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(self.parser.dict_mgr, self.parser)
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict
        self.executor = self.parser.skill_executor

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _plan_step():
        return [{"action": "wait", "seconds": 0.01}]

    def _fallback_skill(self, *, primary="action_plan", **extra):
        skill = {
            "state": "active",
            "learning": {"intent": "TEST_FALLBACK", "slots": []},
            "execution_profile": {
                "primary_route": primary,
                "fallback_routes": ["uia"],
                "max_fallback_attempts": 1,
            },
        }
        skill.update(extra)
        return skill

    # 1. action_plan primary success
    def test_action_plan_primary_success(self):
        skill = {"state": "active", "plan": self._plan_step(),
                 "learning": {"intent": "P", "slots": []}}
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            return_value=_verified(),
        ) as run:
            result = self.executor.execute("시스템", "계획", skill=skill)
        run.assert_called_once()
        diag = result["data"]["skill_execution"]
        self.assertEqual("action_plan", diag["selected_route"])
        self.assertFalse(diag["fallback_used"])
        self.assertTrue(result["success"])

    # 2. python primary success
    def test_python_primary_success(self):
        skill = {"state": "active", "code": "print(1)",
                 "verification_status": "user_confirmed",
                 "learning": {"intent": "PY", "slots": []}}
        with mock.patch.object(
            self.parser.macro_runner, "run",
            return_value=success_result("py", action="python_macro"),
        ) as run:
            result = self.executor.execute("시스템", "파이썬", skill=skill)
        run.assert_called_once()
        self.assertEqual("python", result["data"]["skill_execution"]["selected_route"])

    # 3. primary unavailable -> uia fallback success
    def test_primary_unavailable_falls_back_to_uia(self):
        skill = self._fallback_skill(uia_plan=self._plan_step())  # no "plan"
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            return_value=_verified(action="uia"),
        ) as run:
            result = self.executor.execute("시스템", "폴백", skill=skill)
        run.assert_called_once()
        diag = result["data"]["skill_execution"]
        self.assertTrue(diag["fallback_used"])
        self.assertEqual("uia", diag["selected_route"])
        self.assertEqual(1, diag["fallback_attempts"])
        self.assertEqual("route_unavailable", diag["fallback_reason"])

    # 4. target_not_found -> fallback once
    def test_target_not_found_before_mutation_allows_one_fallback(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        # First call (action_plan) raises a pre-mutation target error; the
        # fallback (uia) succeeds. failed_step is absent -> no state change.
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            side_effect=[FileNotFoundError("대상 없음"), _verified(action="uia")],
        ) as run:
            result = self.executor.execute("시스템", "폴백", skill=skill)
        self.assertEqual(2, run.call_count)
        diag = result["data"]["skill_execution"]
        self.assertTrue(diag["fallback_used"])
        self.assertEqual("target_not_found", diag["fallback_reason"])
        self.assertFalse(diag["state_changed_before_failure"])

    # 5. verification failure -> no fallback
    def test_verification_failure_is_not_retried(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        failed = failure_result(
            "검증 실패", action="action_plan",
            error_type="verification_error", status="verification_failed",
        )
        with mock.patch.object(
            self.parser.action_executor, "execute_plan", return_value=failed,
        ) as run:
            with self.assertRaises(Exception):
                self.executor.execute("시스템", "폴백", skill=skill)
        run.assert_called_once()

    # 6. partial execution -> no fallback
    def test_partial_execution_blocks_fallback(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        mid_run = RuntimeError("2단계 실패")
        mid_run.failed_step = 2  # mutation loop already changed state
        mid_run.error_type = "target_not_found"
        with mock.patch.object(
            self.parser.action_executor, "execute_plan", side_effect=mid_run,
        ) as run:
            with self.assertRaises(RuntimeError):
                self.executor.execute("시스템", "폴백", skill=skill)
        run.assert_called_once()

    # 7. user cancel -> no fallback
    def test_user_cancel_is_never_retried(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            side_effect=ExecutionCancelled("취소"),
        ) as run:
            with self.assertRaises(ExecutionCancelled):
                self.executor.execute("시스템", "폴백", skill=skill)
        run.assert_called_once()

    # 8. destructive/confirmation block -> no fallback
    def test_confirmation_required_blocks_fallback(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            side_effect=ActionConfirmationRequired(
                "덮어쓰기 확인", action="action_plan", target="a.txt",
            ),
        ) as run:
            with self.assertRaises(ActionConfirmationRequired):
                self.executor.execute("시스템", "폴백", skill=skill)
        run.assert_called_once()

    # 9. fallback runs at most once
    def test_fallback_attempts_never_exceed_one(self):
        skill = self._fallback_skill(plan=self._plan_step(),
                                     uia_plan=self._plan_step())
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            side_effect=[FileNotFoundError("1"), FileNotFoundError("2")],
        ) as run:
            with self.assertRaises(FileNotFoundError):
                self.executor.execute("시스템", "폴백", skill=skill)
        # primary + exactly one fallback attempt.
        self.assertEqual(2, run.call_count)

    # 10. only the actual executed route is recorded as a candidate
    def test_only_python_route_records_dynamic_candidate(self):
        # Skill carries Python code but runs via the action_plan route.
        skill = {
            "state": "active",
            "plan": self._plan_step(),
            "code": "print('unused')",
            "verification_status": "user_confirmed",
            "learning": {"intent": "MIXED_ROUTE", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan", return_value=_verified(),
        ), mock.patch.object(
            self.parser.candidate_recording_service, "record_success",
        ) as record_candidate:
            self.executor.execute("시스템", "혼합", skill=skill)
        record_candidate.assert_not_called()

    # 11. existing skill data (no execution_profile) still runs unchanged
    def test_existing_skill_data_stays_compatible(self):
        skill = {"state": "active", "plan": self._plan_step(),
                 "code": "print(1)", "learning": {"intent": "C", "slots": []}}
        with mock.patch.object(
            self.parser.action_executor, "execute_plan", return_value=_verified(),
        ):
            result = self.executor.execute("시스템", "호환", skill=skill)
        diag = result["data"]["skill_execution"]
        self.assertEqual("action_plan", diag["selected_route"])
        self.assertFalse(diag["fallback_used"])
        self.assertEqual(0, diag["fallback_attempts"])

    # 12. success=false return is treated as a failure, not a success
    def test_returned_failure_is_not_counted_as_success(self):
        skill = {"state": "active", "code": "print(1)",
                 "verification_status": "user_confirmed",
                 "learning": {"intent": "RET", "slots": []}}
        returned = failure_result(
            "런타임 실패", action="python_macro", error_type="execution_error",
        )
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=returned,
        ), mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result",
        ) as record, mock.patch.object(
            self.parser.candidate_recording_service, "record_success",
        ) as candidate_success, mock.patch.object(
            self.parser.candidate_recording_service, "record_failure",
        ) as candidate_failure:
            with self.assertRaises(Exception):
                self.executor.execute("시스템", "실패반환", skill=skill)
        record.assert_called_once_with("시스템", "실패반환", False, "execution_error")
        candidate_success.assert_not_called()
        candidate_failure.assert_called_once()

    def test_returned_confirmation_is_not_counted_as_success(self):
        skill = {"state": "active", "code": "print(1)",
                 "verification_status": "user_confirmed",
                 "learning": {"intent": "CONFIRM_RETURN", "slots": []}}
        returned = confirmation_result(
            "확인 필요", {"kind": "test"}, action="python_macro"
        )
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=returned,
        ), mock.patch.object(
            self.parser.dict_mgr, "record_learned_macro_result",
        ) as record, mock.patch.object(
            self.parser.candidate_recording_service, "record_success",
        ) as candidate_success:
            with self.assertRaises(Exception):
                self.executor.execute("시스템", "확인반환", skill=skill)
        candidate_success.assert_not_called()
        record.assert_called_once_with(
            "시스템", "확인반환", False, "execution_error"
        )


if __name__ == "__main__":
    unittest.main()
