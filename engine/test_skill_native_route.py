"""Contract tests for replaying learned native/UIA skills.

Two guarantees:
1. A learned skill that only stores ``native_plan``/``uia_plan`` (no explicit
   execution_profile) selects that route instead of failing route selection.
2. A stored plan that carries an ``app_command`` step fails closed with a clear
   contract message, because replaying it cannot supply the live-inspected
   PreparedAction the native adapters require.
"""

import os
import tempfile
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.execution_result import success_result
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.skills.skill_executor import SkillNativeAppActionUnsupported


def _verified(action="native", message="완료"):
    return success_result(
        message,
        action=action,
        verified=True,
        verification_status="verified",
        verification=[{"status": "verified"}],
    )


class LearnedNativeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-native-route-")
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
    def _deterministic_step():
        return [{"action": "wait", "seconds": 0.01}]

    def _app_command_plan(self):
        return [{
            "action": "app_command",
            "target": "excel",
            "operation": "set_cell_value",
            "params": {"cell": "A1", "value": "10"},
        }]

    # 1. Profile-fallback fix: a native_plan-only skill is now runnable.
    def test_native_plan_only_skill_selects_and_runs_native_route(self):
        skill = {
            "state": "active",
            "native_plan": self._deterministic_step(),
            "learning": {"intent": "NATIVE_ONLY", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            return_value=_verified(action="native"),
        ) as run:
            result = self.executor.execute("엑셀", "네이티브전용", skill=skill)
        run.assert_called_once()
        diag = result["data"]["skill_execution"]
        self.assertEqual("native", diag["selected_route"])
        self.assertFalse(diag["fallback_used"])
        self.assertTrue(result["success"])

    # 2. uia_plan-only skill selects the uia route.
    def test_uia_plan_only_skill_selects_uia_route(self):
        skill = {
            "state": "active",
            "uia_plan": self._deterministic_step(),
            "learning": {"intent": "UIA_ONLY", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            return_value=_verified(action="uia"),
        ) as run:
            result = self.executor.execute("메모장", "유아전용", skill=skill)
        run.assert_called_once()
        self.assertEqual(
            "uia", result["data"]["skill_execution"]["selected_route"]
        )

    # 3. No regression: plan keeps precedence when both keys exist.
    def test_plan_precedence_is_preserved_over_native_plan(self):
        skill = {
            "state": "active",
            "plan": self._deterministic_step(),
            "native_plan": self._deterministic_step(),
            "learning": {"intent": "BOTH", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
            return_value=_verified(action="action_plan"),
        ):
            result = self.executor.execute("엑셀", "둘다", skill=skill)
        self.assertEqual(
            "action_plan", result["data"]["skill_execution"]["selected_route"]
        )

    # 4. Contract: an app_command in a stored plan fails closed and clearly.
    def test_app_command_in_stored_plan_is_rejected_with_clear_contract(self):
        skill = {
            "state": "active",
            "native_plan": self._app_command_plan(),
            "learning": {"intent": "NATIVE_APP_CMD", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
        ) as run:
            with self.assertRaises(SkillNativeAppActionUnsupported) as raised:
                self.executor.execute("엑셀", "네이티브앱", skill=skill)
        run.assert_not_called()
        self.assertEqual("validation_error", raised.exception.error_type)
        self.assertFalse(raised.exception.state_changed)
        self.assertIn("app_command", str(raised.exception))

    # 5. The contract failure never triggers a fallback route.
    def test_app_command_rejection_does_not_fall_back(self):
        skill = {
            "state": "active",
            "native_plan": self._app_command_plan(),
            "uia_plan": self._app_command_plan(),
            "execution_profile": {
                "primary_route": "native",
                "fallback_routes": ["uia"],
                "max_fallback_attempts": 1,
            },
            "learning": {"intent": "NATIVE_APP_CMD_FB", "slots": []},
        }
        with mock.patch.object(
            self.parser.action_executor, "execute_plan",
        ) as run:
            with self.assertRaises(SkillNativeAppActionUnsupported):
                self.executor.execute("엑셀", "폴백앱", skill=skill)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
