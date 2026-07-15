import json
import unittest
import urllib.error
from unittest.mock import patch

from engine.llm_engine import LLMEngine


class DummyDictionary:
    def __init__(self, config):
        self.config = config
        self.noun_dict = {}
        self.search_engines_dict = {}
        self.learned_macros = {}
        self.noun_revision = 0

    def get_ai_config(self):
        return self.config

    def get_nouns(self):
        return self.noun_dict


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LLMFailureTests(unittest.TestCase):
    def test_cloud_mode_without_api_key_returns_explicit_flag(self):
        engine = LLMEngine(DummyDictionary({
            "provider": "openai", "api_key": "", "ollama_model": "llama3"
        }))
        result = engine.process_command("테스트", mode="question", use_api=True)
        self.assertTrue(result["no_api_key"])
        self.assertIn("API 키", result["response"])

    def test_unavailable_ollama_returns_connection_message(self):
        engine = LLMEngine(DummyDictionary({
            "provider": "openai", "api_key": "", "ollama_model": "llama3"
        }))
        with patch("engine.llm_engine.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            result = engine._call_ollama("llama3", "prompt", "input")
        self.assertIn("연결할 수 없어", result["response"])
        self.assertEqual("none", result["action"])

    def test_non_json_command_content_becomes_non_executable_response(self):
        engine = LLMEngine(DummyDictionary({
            "provider": "openai", "api_key": "key", "ollama_model": "llama3"
        }))
        payload = {"choices": [{"message": {"content": "일반 텍스트"}}]}
        with patch("engine.llm_engine.urllib.request.urlopen", return_value=FakeResponse(payload)):
            result = engine._call_openai("key", "prompt", "input", mode="command")
        self.assertEqual("일반 텍스트", result["response"])
        self.assertEqual("none", result["action"])

    def test_unknown_provider_returns_clear_message(self):
        engine = LLMEngine(DummyDictionary({
            "provider": "unknown", "api_key": "key", "ollama_model": "llama3"
        }))
        result = engine.process_command("테스트", mode="question", use_api=True)
        self.assertIn("알 수 없는 AI 제공자", result["response"])


if __name__ == "__main__":
    unittest.main()
