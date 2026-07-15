"""Regression tests for the extracted structured AI action boundary."""

import os
import tempfile
import unittest
from unittest import mock

from engine.ai_actions import AIActionHandler
from engine.builtins import BuiltinMacros
from engine.execution_result import success_result
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


class AIActionHandlerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-ai-handler-")
        self.parser = CommandParser()
        self.parser.dict_mgr = DictionaryManager(
            os.path.join(self.temp_dir.name, "dictionaries.json")
        )
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(self.parser.dict_mgr, self.parser)
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parser_routes_structured_ai_result_through_extracted_handler(self):
        llm_result = {"response": "처리합니다.", "actions": []}
        self.parser.llm_engine.process_command = mock.Mock(return_value=llm_result)
        handled = success_result(
            "분리된 처리기에서 완료했습니다.", action="ai_command", verified=True
        )

        with mock.patch.object(
            self.parser.ai_action_handler, "handle_result", return_value=handled
        ) as handle:
            result = self.parser.execute_command_result(
                "등록되지 않은 경계 분리 테스트 명령",
                mode="command",
                use_api=True,
                session_id="ai-handler-session",
            )

        self.assertIsInstance(self.parser.ai_action_handler, AIActionHandler)
        self.assertEqual("분리된 처리기에서 완료했습니다.", result["message"])
        handle.assert_called_once()
        self.assertIs(llm_result, handle.call_args.args[0])

    def test_blocked_dynamic_action_cannot_bypass_handler_preflight(self):
        result = {
            "response": "레지스트리를 변경합니다.",
            "actions": [{
                "action": "dynamic_code",
                "target": "테스트",
                "app_name": "시스템",
                "macro_name": "blocked_registry_change",
                "description": "차단 확인",
                "code": (
                    "import winreg\n"
                    "winreg.SetValueEx(None, 'x', 0, 1, 'y')"
                ),
                "explanation_steps": [
                    {"step": "변경", "code_snippet": "winreg.SetValueEx(...)"}
                ],
                "learning": {
                    "intent": "REGISTRY_CHANGE",
                    "argument_mode": "json",
                    "verbs": ["변경"],
                    "nouns": [],
                    "utterances": ["레지스트리 변경"],
                    "slots": [],
                },
            }],
        }

        with mock.patch.object(self.parser.macro_runner, "run") as run:
            blocked = self.parser.ai_action_handler.handle_result(
                result,
                "레지스트리 변경",
                session_id="ai-handler-security",
            )

        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("dynamic_code", blocked["action"])
        run.assert_not_called()

    def test_open_then_type_plan_gets_verified_focus_step_and_input_target(self):
        result = {
            "response": "메모장에 입력합니다.",
            "actions": [{
                "action": "action_plan",
                "target": "메모장",
                "app_name": "시스템",
                "macro_name": "notepad_type_test",
                "description": "메모장을 열고 텍스트 입력",
                "plan": [
                    {"action": "open_app", "target": "메모장"},
                    {"action": "type_text", "text": "JARVIS 5-2 테스트"},
                ],
                "learning": {
                    "intent": "TYPE_IN_APP",
                    "argument_mode": "json",
                    "verbs": ["입력"],
                    "nouns": [],
                    "utterances": ["메모장을 열고 테스트 입력"],
                    "slots": [],
                },
            }],
        }

        _, actions, issues = self.parser.ai_action_handler.validate_result(result)

        self.assertEqual([], issues)
        plan = actions[0]["plan"]
        self.assertEqual(
            ["open_app", "focus_window", "type_text"],
            [step["action"] for step in plan],
        )
        self.assertEqual("메모장", plan[1]["target"])
        self.assertEqual("메모장", plan[2]["target"])

    def test_existing_focus_step_is_not_duplicated(self):
        plan = [
            {"action": "open_app", "target": "메모장"},
            {"action": "focus_window", "target": "메모장"},
            {"action": "type_text", "target": "메모장", "text": "테스트"},
        ]

        normalized = self.parser.ai_action_handler._ensure_input_focus_steps(plan)

        self.assertEqual(1, sum(
            step.get("action") == "focus_window" for step in normalized
        ))


if __name__ == "__main__":
    unittest.main()
