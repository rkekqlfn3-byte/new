"""End-to-end tests for learned slot-template reuse."""

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.template_matcher import LearnedTemplateMatcher


def _templated_dynamic_result():
    return {
        "response": "창을 이동했습니다.",
        "actions": [{
            "action": "dynamic_code",
            "app_name": "시스템",
            "macro_name": "창 위치 이동",
            "description": "지정 앱 창을 원하는 방향으로 이동",
            "target": "window-position",
            "code": (
                "import json, sys\n"
                "args = json.loads(sys.argv[1])\n"
                "print(args['app'], args['direction'])\n"
            ),
            "explanation_steps": [{
                "step": "앱과 방향 확인",
                "code_snippet": "args['app'], args['direction']",
            }],
            "learning": {
                "intent": "MOVE_WINDOW",
                "argument_mode": "json",
                "verbs": ["이동해", "옮겨"],
                "nouns": [{"text": "계산기", "canonical": "계산기", "type": "app"}],
                "utterances": [
                    "계산기 창을 오른쪽으로 이동해줘",
                    "{app} 창을 {direction}으로 이동해줘",
                ],
                "slots": [
                    {"name": "app", "type": "app", "value": "계산기", "required": True},
                    {"name": "direction", "type": "direction", "value": "오른쪽", "required": True},
                ],
            },
        }],
    }


class TemplateMatcherTests(unittest.TestCase):
    def _parser(self, path):
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(path)
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser.action_executor)
        return parser

    def test_variant_slots_reuse_learned_code_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-template-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.dict_mgr.add_custom_noun("계산기", "calc")
            parser.dict_mgr.add_custom_noun("메모장", "notepad")
            parser.llm_engine.process_command = mock.Mock(
                return_value=_templated_dynamic_result()
            )

            with mock.patch.object(parser.macro_runner, "run") as run:
                parser.parse_and_execute("계산기 창을 오른쪽으로 이동해줘", use_api=True)
                first_payload = json.loads(run.call_args.args[1])
                self.assertEqual("계산기", first_payload["app"])
                self.assertEqual("오른쪽", first_payload["direction"])
                parser.parse_and_execute("응", use_api=True)

                parser.llm_engine.process_command.reset_mock()
                waiting = parser.execute_command_result(
                    "메모장 창을 왼쪽으로 이동해줘",
                    use_api=True,
                    session_id="template-policy",
                )
                self.assertEqual("confirmation_required", waiting["status"])
                response = parser.resolve_pending_confirmation(
                    "template-policy",
                    waiting["data"]["confirmation"]["confirmation_id"],
                    "run_once",
                )
                second_payload = json.loads(run.call_args.args[1])

            self.assertTrue(response["success"])
            self.assertEqual("메모장", second_payload["app"])
            self.assertEqual("왼쪽", second_payload["direction"])
            self.assertEqual("notepad", second_payload["_app_path"])
            parser.llm_engine.process_command.assert_not_called()

    def test_unknown_app_does_not_match_app_slot(self):
        matcher = LearnedTemplateMatcher()
        learned = {
            "시스템": {
                "창_이동": {
                    "learning": {
                        "utterances": ["{app} 창을 {direction}으로 이동해줘"],
                        "slots": [
                            {"name": "app", "type": "app"},
                            {"name": "direction", "type": "direction"},
                        ],
                    }
                }
            }
        }
        self.assertIsNone(
            matcher.match(
                "없는앱 창을 왼쪽으로 이동해줘",
                learned,
                {"메모장": "notepad"},
            )
        )

    def test_invalid_direction_and_cell_are_rejected(self):
        matcher = LearnedTemplateMatcher()
        learned = {
            "엑셀": {
                "셀_이동": {
                    "learning": {
                        "utterances": ["{cell} 셀을 {direction}으로 이동해"],
                        "slots": [
                            {"name": "cell", "type": "cell"},
                            {"name": "direction", "type": "direction"},
                        ],
                    }
                }
            }
        }
        self.assertIsNotNone(matcher.match("A12 셀을 아래로 이동해", learned, {}))
        self.assertIsNone(matcher.match("열둘 셀을 대각선으로 이동해", learned, {}))

    def test_korean_particles_change_with_captured_noun(self):
        matcher = LearnedTemplateMatcher()
        learned = {
            "시스템": {
                "창_이동": {
                    "learning": {
                        "utterances": ["{app}을 {direction}으로 옮겨"],
                        "slots": [
                            {"name": "app", "type": "app"},
                            {"name": "direction", "type": "direction"},
                        ],
                    }
                }
            }
        }
        nouns = {"계산기": "calc", "메모장": "notepad"}
        self.assertIsNotNone(matcher.match("계산기를 아래로 옮겨", learned, nouns))
        matcher.invalidate()
        self.assertIsNotNone(matcher.match("메모장을 오른쪽으로 옮겨", learned, nouns))

    def test_cached_matching_stays_fast_with_many_templates(self):
        matcher = LearnedTemplateMatcher()
        macros = {}
        for index in range(300):
            macros[f"명령_{index}"] = {
                "learning": {
                    "utterances": [f"{{app}} 창을 오른쪽으로 이동해 {index}"],
                    "slots": [{"name": "app", "type": "app"}],
                }
            }
        learned = {"시스템": macros}
        nouns = {"메모장": "notepad"}
        self.assertIsNotNone(matcher.match("메모장 창을 오른쪽으로 이동해 299", learned, nouns))
        started = time.perf_counter()
        for _ in range(100):
            self.assertIsNotNone(
                matcher.match("메모장 창을 오른쪽으로 이동해 299", learned, nouns)
            )
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
