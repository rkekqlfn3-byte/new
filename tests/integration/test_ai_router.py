"""Tests for local-first, local-only, and AI-first routing policies."""

import os
import tempfile
import unittest
from datetime import datetime as real_datetime
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


class RouterDictionary:
    def __init__(self, routing_mode="auto", api_key="key", provider="gemini"):
        self.config = {
            "provider": provider,
            "api_key": api_key,
            "routing_mode": routing_mode,
        }
        self.noun_dict = {"메모장": "notepad"}
        self.search_engines_dict = {}
        self.learned_macros = {}
        self.noun_revision = 0

    def get_ai_config(self):
        return self.config

    def get_nouns(self):
        return self.noun_dict


class LLMRouterTests(unittest.TestCase):
    def test_auto_command_uses_configured_cloud_provider(self):
        engine = LLMEngine(RouterDictionary(routing_mode="auto", api_key="key"))
        with mock.patch.object(
            engine, "_call_gemini", return_value={"response": "cloud", "actions": []}
        ) as cloud:
            result = engine.process_command("새 명령", mode="command", use_api=False)
        self.assertEqual("cloud", result["response"])
        cloud.assert_called_once()

    def test_auto_command_without_key_returns_explicit_error(self):
        engine = LLMEngine(RouterDictionary(routing_mode="auto", api_key=""))
        with mock.patch.object(engine, "_call_gemini") as cloud:
            result = engine.process_command("새 명령", mode="command", use_api=True)
        self.assertTrue(result["no_api_key"])
        self.assertIn("API 키", result["response"])
        cloud.assert_not_called()

    def test_cloud_failure_is_returned_without_local_provider_fallback(self):
        engine = LLMEngine(RouterDictionary(routing_mode="auto", api_key="key"))
        with mock.patch.object(
            engine, "_call_gemini",
            return_value={"response": "cloud failed", "provider_error": True},
        ) as cloud:
            result = engine.process_command("새 명령", mode="command", use_api=False)
        self.assertEqual("cloud failed", result["response"])
        self.assertTrue(result["provider_error"])
        self.assertEqual("gemini", result["routing"])
        cloud.assert_called_once()

    def test_conversation_mode_always_uses_configured_cloud_provider(self):
        engine = LLMEngine(RouterDictionary(routing_mode="auto", api_key="key"))
        with mock.patch.object(
            engine, "_call_gemini",
            return_value={"response": "conversation", "action": "none"},
        ) as cloud:
            engine.process_command("대화", mode="conversation", use_api=False)
        cloud.assert_called_once()


class ParserRouterTests(unittest.TestCase):
    def _parser(self, path, routing_mode):
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(path)
        parser.dict_mgr.ai_config["routing_mode"] = routing_mode
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser)
        return parser

    def test_local_only_unknown_command_never_calls_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "local_only")
            parser.llm_engine.process_command = mock.Mock()
            response = parser.parse_and_execute("등록되지 않은 완전히 새로운 명령")
        self.assertIn("로컬 전용 모드", response)
        parser.llm_engine.process_command.assert_not_called()

    def test_ai_first_bypasses_known_local_command(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "ai_first")
            parser.llm_engine.process_command = mock.Mock(
                return_value={"response": "AI가 분석함", "actions": []}
            )
            with mock.patch("engine.parser.os.startfile") as startfile:
                response = parser.parse_and_execute("메모장 열어")
        self.assertEqual("AI가 분석함", response)
        startfile.assert_not_called()
        parser.llm_engine.process_command.assert_called_once()

    def test_command_ai_fallback_forwards_summary_and_reference_state(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "ai_first")
            parser.llm_engine.process_command = mock.Mock(
                return_value={"response": "문맥을 확인했습니다.", "actions": []}
            )
            state = {"recent_turns": [{
                "user_request": "메모장 열어줘",
                "result": {"action": "open_app", "target": "메모장"},
            }]}

            parser.execute_command_result(
                "방금 거 다시 해줘",
                mode="command",
                summary="이전 요약",
                conversation_state=state,
            )

        call = parser.llm_engine.process_command.call_args
        self.assertEqual("이전 요약", call.kwargs["summary"])
        self.assertEqual(state, call.kwargs["conversation_state"])

    def test_auto_mode_keeps_known_command_local(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict["메모장"] = "notepad"
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.parser.os.startfile") as startfile, \
                 mock.patch.object(
                     parser.action_executor,
                     "focus_window_when_ready",
                     return_value=101,
                 ) as focus:
                parser.parse_and_execute("메모장 열어")
        startfile.assert_called_once_with("notepad")
        focus.assert_called_once_with("메모장")
        parser.llm_engine.process_command.assert_not_called()

    def test_missing_open_target_is_rediscovered_once_before_execution(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict.pop("sampleapp", None)
            parser.dict_mgr.noun_revision += 1
            parser.llm_engine.process_command = mock.Mock()

            def discover(candidates):
                self.assertIn("sampleapp", candidates)
                parser.dict_mgr.noun_dict["sampleapp"] = (
                    r"C:\Program Files\SampleApp\SampleApp.exe"
                )
                parser.dict_mgr.noun_revision += 1
                return 1

            parser.dict_mgr.discover_apps = mock.Mock(side_effect=discover)
            with (
                mock.patch("engine.builtins.os.startfile") as startfile,
                mock.patch.object(
                    parser.action_executor,
                    "focus_window_when_ready",
                    return_value=701,
                ),
            ):
                result = parser.execute_command_result("sampleapp 열어줘")

        self.assertTrue(result["success"])
        self.assertEqual("open_app", result["action"])
        self.assertEqual(
            "recovered",
            result["data"]["automatic_recovery"]["outcome"],
        )
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual(1, recovery["schema_version"])
        self.assertEqual("pre_execution", recovery["phase"])
        self.assertFalse(recovery["execution_started"])
        self.assertTrue(recovery["target_unchanged"])
        self.assertTrue(recovery["target_resolved"])
        self.assertEqual(1, recovery["retry_limit"])
        self.assertEqual(1, result["data"]["retry_count"])
        parser.dict_mgr.discover_apps.assert_called_once()
        startfile.assert_called_once_with(
            r"C:\Program Files\SampleApp\SampleApp.exe"
        )
        parser.llm_engine.process_command.assert_not_called()

    def test_changed_target_after_rediscovery_is_not_executed(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict.pop("sampleapp", None)
            parser.dict_mgr.noun_revision += 1
            parser.dict_mgr.discover_apps = mock.Mock(return_value=1)
            parser.llm_engine.process_command = mock.Mock()
            original_analyze = parser.analyze_command
            analyze_count = 0

            def changed_analysis(text):
                nonlocal analyze_count
                analyze_count += 1
                value = original_analyze(text)
                if analyze_count >= 2:
                    value = dict(value)
                    value["macro"] = "CLOSE"
                return value

            parser.analyze_command = mock.Mock(side_effect=changed_analysis)
            with mock.patch("engine.builtins.os.startfile") as startfile:
                result = parser.execute_command_result("sampleapp 열어줘")

        self.assertFalse(result["success"])
        recovery = result["data"]["automatic_recovery"]
        self.assertEqual("target_changed", recovery["outcome"])
        self.assertFalse(recovery["execution_started"])
        self.assertFalse(recovery["target_unchanged"])
        self.assertFalse(recovery["target_resolved"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertIn("자동 실행을 멈췄습니다", result["message"])
        startfile.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_exhausted_app_rediscovery_stops_and_requests_target_information(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict.pop("missingapp", None)
            parser.dict_mgr.noun_revision += 1
            parser.dict_mgr.discover_apps = mock.Mock(return_value=0)
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.os.startfile") as startfile:
                result = parser.execute_command_result("missingapp 열어줘")

        self.assertFalse(result["success"])
        self.assertEqual("target_not_found", result["error_type"])
        self.assertEqual(
            "not_found",
            result["data"]["automatic_recovery"]["outcome"],
        )
        self.assertEqual(1, result["data"]["retry_count"])
        self.assertIn("Windows 앱 목록도 다시 확인", result["message"])
        parser.dict_mgr.discover_apps.assert_called_once()
        startfile.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_question_mode_current_date_uses_local_clock_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.datetime") as local_datetime:
                local_datetime.now.return_value = real_datetime(2026, 7, 14, 15, 30)
                result = parser.execute_command_result(
                    [
                        {"role": "assistant", "content": "무엇을 도와드릴까요?"},
                        {"role": "user", "content": "오늘 날짜 알려줘"},
                    ],
                    mode="question",
                    use_api=True,
                )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("date", result["action"])
        self.assertEqual("오늘은 2026년 7월 14일 화요일입니다.", result["message"])
        parser.llm_engine.process_command.assert_not_called()

    def test_question_mode_current_time_uses_local_clock_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.datetime") as local_datetime:
                local_datetime.now.return_value = real_datetime(2026, 7, 17, 20, 27)
                result = parser.execute_command_result(
                    [{"role": "user", "content": "지금 몇 시야?"}],
                    mode="question",
                    use_api=True,
                )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("time", result["action"])
        self.assertEqual("지금은 오후 8시 27분입니다.", result["message"])
        parser.llm_engine.process_command.assert_not_called()

    def test_question_mode_current_weather_stays_local(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.os.startfile") as startfile:
                result = parser.execute_command_result(
                    [{"role": "user", "content": "오늘 날씨 알려줘"}],
                    mode="question",
                    use_api=True,
                )

        self.assertTrue(result["success"])
        self.assertEqual("weather", result["action"])
        startfile.assert_called_once_with(
            "https://search.naver.com/search.naver?query=오늘날씨"
        )
        parser.llm_engine.process_command.assert_not_called()

    def test_command_mode_current_date_uses_local_clock_without_ai_or_learned_code(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            parser.dict_mgr.macro_dict["날짜보기"] = {
                "type": "learned",
                "app": "시스템",
                "synonyms": ["오늘 날짜 알려줘"],
            }
            parser.dict_mgr.learned_macros = {
                "시스템": {"날짜보기": {"code": "message_box()", "plan": []}}
            }
            with mock.patch("engine.builtins.datetime") as local_datetime, \
                 mock.patch.object(parser.macro_runner, "run") as run:
                local_datetime.now.return_value = real_datetime(2026, 7, 17, 9, 0)
                result = parser.execute_command_result(
                    "오늘 날짜 알려줘",
                    mode="command",
                    session_id="local-date-command",
                )

        self.assertTrue(result["success"])
        self.assertEqual("date", result["action"])
        self.assertEqual("오늘은 2026년 7월 17일 금요일입니다.", result["message"])
        parser.llm_engine.process_command.assert_not_called()
        run.assert_not_called()

    def test_explicit_volume_percentage_and_unmute_stay_local(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.set_system_volume", return_value=30) as set_volume:
                volume = parser.execute_command_result("볼륨을 30%로 설정해줘")
            with mock.patch("engine.builtins.set_system_muted", return_value=False) as set_muted:
                unmute = parser.execute_command_result("음소거 해제해줘")

        self.assertTrue(volume["success"])
        self.assertEqual("volume_set", volume["action"])
        self.assertEqual("시스템 볼륨을 30%로 설정했습니다.", volume["message"])
        set_volume.assert_called_once_with(30)
        self.assertTrue(unmute["success"])
        self.assertEqual("unmute", unmute["action"])
        set_muted.assert_called_once_with(False)
        parser.llm_engine.process_command.assert_not_called()

    def test_relative_and_small_volume_requests_adjust_instead_of_set(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch(
                "engine.builtins.adjust_system_volume",
                side_effect=[(30, 40), (40, 30), (30, 35)],
            ) as adjust_volume, mock.patch(
                "engine.builtins.set_system_volume"
            ) as set_volume:
                raised = parser.execute_command_result("볼륨 10만큼 올려줘")
                lowered = parser.execute_command_result("볼륨 10만큼 내려줘")
                small = parser.execute_command_result("소리를 조금만 올려줘")

        self.assertEqual("volume_up", raised["action"])
        self.assertEqual("volume_down", lowered["action"])
        self.assertEqual("volume_up", small["action"])
        self.assertEqual(
            [mock.call(10), mock.call(-10), mock.call(5)],
            adjust_volume.call_args_list,
        )
        set_volume.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_out_of_range_volume_is_blocked_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.set_system_volume") as set_volume:
                result = parser.execute_command_result("볼륨 120%로 설정해줘")

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("volume_set", result["action"])
        set_volume.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_shell_request_is_blocked_before_echo_app_matching(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict["echo"] = r"C:\Program Files\Git\usr\bin\echo.exe"
            parser.dict_mgr.noun_revision += 1
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.os.startfile") as startfile:
                result = parser.execute_command_result(
                    "명령 프롬프트에서 echo JARVIS_TEST 실행해줘"
                )

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("command_line", result["action"])
        startfile.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_console_executable_cannot_bypass_policy_as_open_app(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict["echo"] = r"C:\Program Files\Git\usr\bin\echo.exe"
            parser.dict_mgr.noun_revision += 1
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.builtins.os.startfile") as startfile:
                result = parser.execute_command_result("echo 실행해줘")

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("open_app", result["action"])
        startfile.assert_not_called()
        parser.llm_engine.process_command.assert_not_called()

    def test_question_mode_non_date_question_still_calls_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.llm_engine.process_command = mock.Mock(
                return_value={"response": "일반 답변", "action": "none"}
            )
            result = parser.execute_command_result(
                [{"role": "user", "content": "엑셀 함수란 뭐야?"}],
                mode="question",
                use_api=True,
            )

        self.assertEqual("일반 답변", result["message"])
        parser.llm_engine.process_command.assert_called_once()


if __name__ == "__main__":
    unittest.main()
