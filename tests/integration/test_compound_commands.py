import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine.parser import CommandParser
from engine.execution_result import failure_result
from engine.managers.dict_manager import DictionaryManager


class CompoundCommandTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()
        self.parser.dict_mgr.noun_dict = {
            "유튜브": "https://www.youtube.com",
            "메모장": "notepad.exe",
            "계산기": "calc.exe",
            "카톡": "KakaoTalk.exe",
        }
        self.parser.dict_mgr.search_engines_dict = {
            "구글": "https://www.google.com/search?q=",
            "네이버": "https://search.naver.com/search.naver?query=",
        }
        self.events = []

        def record(action):
            def handler(user_input, tokens, app_name, app_path, log_callback):
                self.events.append((action, app_name, user_input))
                return f"{action} 완료"
            return handler

        self.parser._handlers["OPEN"] = record("OPEN")
        self.parser._handlers["VOL_DOWN"] = record("VOL_DOWN")
        self.parser._handlers["PLAYPAUSE"] = record("PLAYPAUSE")

    def test_korean_connective_runs_steps_in_order(self):
        result = self.parser.parse_and_execute("유튜브 켜고 볼륨 줄여줘")

        self.assertEqual(
            [("OPEN", "유튜브"), ("VOL_DOWN", None)],
            [(action, app) for action, app, _ in self.events],
        )
        self.assertIn("✅ 1.", result)
        self.assertIn("✅ 2.", result)

    def test_irregular_next_form_is_normalized(self):
        self.parser.parse_and_execute("메모장 연 다음 계산기 켜줘")

        self.assertEqual(
            [("OPEN", "메모장"), ("OPEN", "계산기")],
            [(action, app) for action, app, _ in self.events],
        )

    def test_media_then_app_command_is_local(self):
        self.parser.parse_and_execute("노래 멈추고 카톡 열어줘")

        self.assertEqual(
            [("PLAYPAUSE", None), ("OPEN", "카톡")],
            [(action, app) for action, app, _ in self.events],
        )

    def test_search_then_open_runs_in_order(self):
        opened = []
        with patch("engine.builtins.os.startfile", side_effect=opened.append):
            result = self.parser.parse_and_execute(
                "네이버에서 날씨 검색하고 메모장 열어줘"
            )

        self.assertTrue(opened[0].endswith("%EB%82%A0%EC%94%A8"))
        self.assertEqual("OPEN", self.events[-1][0])
        self.assertIn("✅ 2.", result)

    def test_unknown_step_falls_back_before_any_local_execution(self):
        self.parser.llm_engine.process_command = Mock(
            return_value={"response": "AI fallback", "actions": []}
        )

        result = self.parser.parse_and_execute("유튜브 켜고 알 수 없는 작업 해줘")

        self.assertEqual([], self.events)
        self.assertEqual("AI fallback", result)
        self.parser.llm_engine.process_command.assert_called_once()

    def test_learned_open_and_type_template_executes_locally_as_one_action(self):
        temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-compound-learned-")
        self.addCleanup(temp_dir.cleanup)
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(
            os.path.join(temp_dir.name, "dictionaries.json")
        )
        parser.dict_mgr.noun_dict = {"메모장": "notepad.exe"}
        macro_name = "메모장_텍스트_입력"
        parser.dict_mgr.macro_dict[macro_name] = {
            "name": macro_name,
            "type": "learned",
            "app": "메모장",
        }
        parser.dict_mgr.learned_macros = {
            "메모장": {
                macro_name: {
                    "state": "active",
                    "code": "",
                    "plan": [{"action": "wait", "seconds": 0.1}],
                    "learning": {
                        "intent": "OPEN_APP_AND_TYPE_TEXT",
                        "utterances": [
                            "{app}을 열고 {text_to_type}라고 입력해줘"
                        ],
                        "slots": [
                            {"name": "app", "type": "app", "required": True},
                            {
                                "name": "text_to_type",
                                "type": "text",
                                "required": True,
                            },
                        ],
                    },
                    "verification_status": "user_confirmed",
                }
            }
        }
        parser.template_matcher.invalidate()
        parser.llm_engine.process_command = Mock(
            side_effect=AssertionError("AI should not run")
        )
        execution = {
            "success": True,
            "verified": True,
            "verification_status": "verified",
        }

        with patch.object(
            parser.action_executor,
            "execute_plan",
            return_value=execution,
        ) as execute_plan:
            waiting = parser.execute_command_result(
                "메모장을 열고 JARVIS 5-4 학습 확인이라고 입력해줘",
                session_id="compound-policy",
            )
            self.assertEqual("confirmation_required", waiting["status"])
            result = parser.resolve_pending_confirmation(
                "compound-policy",
                waiting["data"]["confirmation"]["confirmation_id"],
                "run_once",
            )

        self.assertTrue(result["success"])
        parser.llm_engine.process_command.assert_not_called()
        slots = execute_plan.call_args.args[1]
        self.assertEqual("메모장", slots["app"])
        self.assertEqual("jarvis 5-4 학습 확인", slots["text_to_type"])

    def test_sequence_stops_and_marks_failed_step(self):
        def fail_open(user_input, tokens, app_name, app_path, log_callback):
            self.events.append(("OPEN_FAILED", app_name, user_input))
            return failure_result(
                "앱 실행에 실패했습니다.", action="open_app",
                target=app_name, error_type="execution_error",
            )

        self.parser._handlers["OPEN"] = fail_open
        result = self.parser.parse_and_execute("유튜브 켜고 계산기 켜줘")

        self.assertEqual(1, len(self.events))
        self.assertIn("❌ 1.", result)
        self.assertNotIn("2.", result)


if __name__ == "__main__":
    unittest.main()
