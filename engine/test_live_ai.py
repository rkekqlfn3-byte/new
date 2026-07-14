"""Opt-in smoke test for the AI provider configured in the user's Jarvis data."""

import os
import unittest

from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager


@unittest.skipUnless(
    os.environ.get("JARVIS_LIVE_AI_TEST") == "1",
    "Set JARVIS_LIVE_AI_TEST=1 to call the configured AI provider",
)
class LiveAIIntegrationTests(unittest.TestCase):
    def test_configured_provider_returns_a_response(self):
        manager = DictionaryManager()
        config = manager.get_ai_config()
        provider = config.get("provider", "openai")
        if provider in {"openai", "gemini"} and not config.get("api_key"):
            self.skipTest(f"No API key configured for {provider}")

        result = LLMEngine(manager).process_command(
            "연결 확인입니다. JARVIS_OK라고 짧게 답하세요.",
            mode="question",
            use_api=True,
        )
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("no_api_key", False))
        response = result.get("response", "")
        self.assertIsInstance(response, str)
        self.assertTrue(response.strip())
        self.assertNotIn("연결에 실패", response)
        self.assertNotIn("API 에러", response)

    def test_configured_provider_accepts_structured_command_schema(self):
        manager = DictionaryManager()
        config = manager.get_ai_config()
        provider = config.get("provider", "openai")
        if provider in {"openai", "gemini"} and not config.get("api_key"):
            self.skipTest(f"No API key configured for {provider}")

        result = LLMEngine(manager).process_command(
            "메모장을 열어줘",
            mode="command",
            use_api=False,
        )
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get("actions"), list)
        self.assertTrue(result["actions"])
        self.assertEqual("open_app", result["actions"][0].get("action"))
        self.assertEqual(provider, result.get("routing"))

    def test_new_automation_prefers_common_action_plan(self):
        manager = DictionaryManager()
        config = manager.get_ai_config()
        provider = config.get("provider", "openai")
        if provider in {"openai", "gemini"} and not config.get("api_key"):
            self.skipTest(f"No API key configured for {provider}")

        result = LLMEngine(manager).process_command(
            "계산기 창을 화면 오른쪽 절반으로 이동해줘",
            mode="command",
            use_api=True,
        )
        actions = result.get("actions", []) if isinstance(result, dict) else []
        planned = [item for item in actions if item.get("action") == "action_plan"]
        self.assertTrue(planned, result.get("response", "") if isinstance(result, dict) else result)
        learning = planned[0].get("learning")
        self.assertIsInstance(learning, dict)
        self.assertTrue(str(learning.get("intent", "")).strip())
        self.assertEqual("json", learning.get("argument_mode"))
        self.assertIsInstance(learning.get("verbs"), list)
        self.assertIsInstance(learning.get("nouns"), list)
        self.assertIsInstance(learning.get("utterances"), list)
        self.assertIsInstance(learning.get("slots"), list)
        self.assertIsInstance(planned[0].get("plan"), list)
        self.assertTrue(planned[0]["plan"])
        self.assertFalse(planned[0].get("code"))


if __name__ == "__main__":
    unittest.main()
