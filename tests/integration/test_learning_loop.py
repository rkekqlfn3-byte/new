"""Regression tests for the local-first learned macro connection."""

import json
import os
import tempfile
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


def _dynamic_action(name="창 정리!", target="primary-display"):
    return {
        "response": "실행했습니다.",
        "actions": [
            {
                "action": "dynamic_code",
                "app_name": "시스템",
                "macro_name": name,
                "description": "창을 지정 위치에 정리",
                "target": target,
                "code": (
                    "import json, sys\n"
                    "args = json.loads(sys.argv[1])\n"
                    "value = args['_target']\n"
                    "print(value)\n"
                ),
                "explanation_steps": [
                    {"step": "대상 확인", "code_snippet": "value = args['_target']"}
                ],
                "learning": {
                    "intent": "move-window",
                    "argument_mode": "json",
                    "verbs": ["정리해", "옮겨", "정리해"],
                    "nouns": [
                        {"text": "계산기앱", "canonical": "계산기", "type": "app"},
                        {"text": "가짜앱", "canonical": "없는앱", "type": "app"},
                        {"text": "오른쪽", "canonical": "", "type": "general"},
                    ],
                    "utterances": [
                        "계산기앱을 오른쪽으로 정리해",
                        "{app}을 {direction}으로 정리해",
                    ],
                    "slots": [
                        {"name": "direction", "type": "direction", "value": "오른쪽", "required": True}
                    ],
                },
            }
        ],
    }


class LearningLoopTests(unittest.TestCase):
    def _parser_with_temp_dictionary(self, path):
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(path)
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser)
        return parser

    def test_approved_sentence_runs_locally_next_time_with_saved_target(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-learning-test-") as temp_dir:
            parser = self._parser_with_temp_dictionary(
                os.path.join(temp_dir, "dictionaries.json")
            )
            parser.llm_engine.process_command = mock.Mock(return_value=_dynamic_action())
            command = "창을 특별하게 정리해줘"

            with mock.patch.object(parser.macro_runner, "run") as run:
                first = parser.parse_and_execute(command, use_api=True)
                self.assertIn("학습", first)
                self.assertEqual(1, len(parser.pending_macros))

                approved = parser.parse_and_execute("응", use_api=True)
                self.assertIn("저장", approved)
                self.assertIn("창_정리", parser.dict_mgr.macro_dict)
                self.assertIn(
                    command,
                    parser.dict_mgr.macro_dict["창_정리"]["synonyms"],
                )
                self.assertEqual(
                    "primary-display",
                    parser.dict_mgr.learned_macros["시스템"]["창_정리"]["default_target"],
                )
                learned = parser.dict_mgr.learned_macros["시스템"]["창_정리"]
                self.assertEqual("MOVE_WINDOW", learned["learning"]["intent"])
                self.assertEqual("user_confirmed", learned["verification_status"])
                self.assertEqual(["정리해", "옮겨"], learned["learning"]["verbs"])
                self.assertEqual("오른쪽", learned["learning"]["slots"][0]["value"])
                self.assertEqual("calc", parser.dict_mgr.noun_dict["계산기앱"])
                self.assertNotIn("가짜앱", parser.dict_mgr.noun_dict)
                self.assertNotIn(
                    "{app}을 {direction}으로 정리해",
                    parser.dict_mgr.macro_dict["창_정리"]["synonyms"],
                )

                parser.llm_engine.process_command.reset_mock()
                waiting = parser.execute_command_result(
                    command, use_api=True, session_id="learning-policy"
                )
                self.assertEqual("confirmation_required", waiting["status"])
                second = parser.resolve_pending_confirmation(
                    "learning-policy",
                    waiting["data"]["confirmation"]["confirmation_id"],
                    "run_once",
                )

            self.assertTrue(second["success"])
            parser.llm_engine.process_command.assert_not_called()
            learned_argument = json.loads(run.call_args_list[-1].args[1])
            self.assertEqual("primary-display", learned_argument["_target"])
            self.assertEqual(
                1,
                parser.dict_mgr.learned_macros["시스템"]["창_정리"]["usage_count"],
            )
            self.assertEqual(
                1,
                parser.dict_mgr.learned_macros["시스템"]["창_정리"]["success_count"],
            )

            reloaded = DictionaryManager(os.path.join(temp_dir, "dictionaries.json"))
            self.assertEqual(
                "MOVE_WINDOW",
                reloaded.learned_macros["시스템"]["창_정리"]["learning"]["intent"],
            )

    def test_compound_dynamic_actions_do_not_all_claim_the_whole_sentence(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-learning-test-") as temp_dir:
            parser = self._parser_with_temp_dictionary(
                os.path.join(temp_dir, "dictionaries.json")
            )
            result = _dynamic_action("first")
            result["actions"].append(_dynamic_action("second")["actions"][0])
            parser.llm_engine.process_command = mock.Mock(return_value=result)

            with mock.patch.object(parser.macro_runner, "run"):
                compound_command = "첫 작업하고 두 번째 작업해"
                parser.parse_and_execute(compound_command, use_api=True)

            self.assertEqual(2, len(parser.pending_macros))
            self.assertTrue(
                all(compound_command not in item["utterances"] for item in parser.pending_macros)
            )

    def test_legacy_learned_macros_are_reconnected_and_empty_names_repaired(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-learning-test-") as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            with open(path, "w", encoding="utf-8") as output:
                json.dump(
                    {
                        "noun_dictionary": {},
                        "macro_dictionary": {},
                        "learned_macros": {
                            "시스템": {
                                "": {"description": "이름 없는 기존 코드", "code": "print('ok')"},
                                "기존명령": {
                                    "description": "기존 코드",
                                    "code": "print('ok')",
                                    "utterances": ["내 기존 명령 실행해"],
                                },
                            }
                        },
                    },
                    output,
                    ensure_ascii=False,
                )

            manager = DictionaryManager(path)

            self.assertNotIn("", manager.learned_macros["시스템"])
            repaired_names = set(manager.learned_macros["시스템"])
            self.assertIn("기존명령", repaired_names)
            self.assertTrue(any(name.startswith("learned_macro") for name in repaired_names))
            self.assertEqual("learned", manager.macro_dict["기존명령"]["type"])
            self.assertIn("내 기존 명령 실행해", manager.macro_dict["기존명령"]["synonyms"])


if __name__ == "__main__":
    unittest.main()
