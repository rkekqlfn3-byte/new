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

    def test_auto_mode_keeps_known_command_local(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-router-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionary.json"), "auto")
            parser.dict_mgr.noun_dict["메모장"] = "notepad"
            parser.llm_engine.process_command = mock.Mock()
            with mock.patch("engine.parser.os.startfile") as startfile:
                parser.parse_and_execute("메모장 열어")
        startfile.assert_called_once_with("notepad")
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
