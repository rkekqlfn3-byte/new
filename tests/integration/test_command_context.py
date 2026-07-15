import json
import time
import unittest
from unittest import mock

from engine.command_context import CommandContextBuilder
from engine.llm_engine import LLMEngine
from engine.parser import CommandParser


class ContextDictionary:
    def __init__(self):
        self.noun_dict = {}
        self.search_engines_dict = {}
        self.favorites = []
        self.learned_macros = {}
        self.noun_revision = 1
        self.config = {
            "provider": "openai", "api_key": "test", "ollama_model": "llama3",
            "routing_mode": "auto",
        }

    def get_nouns(self):
        return self.noun_dict

    def get_ai_config(self):
        return self.config


def _assert_forbidden_keys(test_case, value):
    if isinstance(value, dict):
        for key, child in value.items():
            test_case.assertNotIn(key, {"code", "plan", "explanation_steps"})
            _assert_forbidden_keys(test_case, child)
    elif isinstance(value, list):
        for child in value:
            _assert_forbidden_keys(test_case, child)


class CommandContextBuilderTests(unittest.TestCase):
    def test_exact_app_is_kept_and_candidates_are_capped(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {f"테스트앱{i:04d}": f"app{i}.exe" for i in range(5000)}
        dictionary.noun_dict["정확한앱"] = "exact.exe"
        builder = CommandContextBuilder(dictionary)

        context = builder.build("정확한앱을 열고 테스트앱을 찾아줘")
        payload = json.loads(context.dictionary_context)
        names = [item["name"] for item in payload["apps"]]

        self.assertIn("정확한앱", names)
        self.assertLessEqual(len(names), 20)

    def test_irrelevant_apps_are_not_used_as_padding(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {"메모장": "notepad", "계산기": "calc"}
        context = CommandContextBuilder(dictionary).build("오늘 날짜를 알려줘")
        self.assertEqual(0, context.app_candidates)

    def test_small_typo_can_find_relevant_app(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {"메모장": "notepad", "계산기": "calc"}
        context = CommandContextBuilder(dictionary).build("메모쟝 열어")
        self.assertIn("메모장", context.allowed_apps)

    def test_learned_context_is_capped_and_contains_no_executable_body(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {"엑셀": "excel.exe"}
        dictionary.learned_macros = {"엑셀": {}}
        for index in range(500):
            dictionary.learned_macros["엑셀"][f"셀색칠{index}"] = {
                "code": f"print({index})",
                "plan": [{"action": "type_text", "text": "secret"}],
                "description": "엑셀 셀을 노란색으로 색칠",
                "learning": {
                    "intent": "SET_CELL_COLOR",
                    "verbs": ["색칠"],
                    "utterances": [f"엑셀 셀색칠{index}"],
                    "slots": [],
                },
            }
        context = CommandContextBuilder(dictionary).build("엑셀 셀색칠12 해줘")
        payload = json.loads(context.learned_macros_context)

        self.assertLessEqual(len(payload["macros"]), 8)
        self.assertIn(("엑셀", "셀색칠12"), context.allowed_macros)
        _assert_forbidden_keys(self, payload)
        self.assertNotIn("print(", context.learned_macros_context)
        self.assertNotIn("secret", context.learned_macros_context)

    def test_large_dictionary_does_not_expand_context(self):
        small = ContextDictionary()
        small.noun_dict = {"정확한앱": "exact.exe"}
        large = ContextDictionary()
        large.noun_dict = {f"무관앱{i:04d}": f"app{i}.exe" for i in range(5000)}
        large.noun_dict["정확한앱"] = "exact.exe"

        small_context = CommandContextBuilder(small).build("정확한앱 열어")
        started = time.perf_counter()
        large_context = CommandContextBuilder(large).build("정확한앱 열어")
        elapsed = time.perf_counter() - started

        self.assertEqual(small_context.dictionary_context, large_context.dictionary_context)
        self.assertLess(elapsed, 1.5)

    def test_legacy_macro_can_be_found_from_description_without_code(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {"엑셀": "excel.exe"}
        dictionary.learned_macros = {
            "엑셀": {
                "internal_name": {
                    "description": "활성 시트의 A1 셀 배경색을 노란색으로 변경",
                    "code": "print('must not leak')",
                }
            }
        }
        context = CommandContextBuilder(dictionary).build(
            "엑셀 A1 셀을 노란색으로 변경해줘"
        )
        self.assertIn(("엑셀", "internal_name"), context.allowed_macros)
        self.assertNotIn("must not leak", context.learned_macros_context)


class CommandContextIntegrationTests(unittest.TestCase):
    def test_llm_receives_filtered_context_and_returns_candidate_metadata(self):
        dictionary = ContextDictionary()
        dictionary.noun_dict = {"메모장": "notepad", "계산기": "calc"}
        engine = LLMEngine(dictionary)
        captured = {}

        def fake_provider(*args, **kwargs):
            captured["prompt"] = args[3]
            return {"response": "ok", "actions": []}

        with mock.patch.object(engine, "_invoke_provider", side_effect=fake_provider):
            result = engine.process_command("메모장 열어", mode="command")

        self.assertIn("메모장", captured["prompt"])
        self.assertNotIn('"name":"계산기"', captured["prompt"])
        self.assertEqual(["메모장"], result["_allowed_app_candidates"])
        self.assertEqual(1, engine.last_command_context_stats["app_candidates"])
        self.assertGreater(engine.last_command_context_stats["prompt_chars"], 0)

    def test_parser_rejects_app_outside_ai_candidates(self):
        parser = CommandParser()
        parser.dict_mgr.noun_dict = {"메모장": "notepad", "계산기": "calc"}
        parser.dict_mgr.noun_revision += 1
        result = {
            "response": "실행합니다.",
            "_allowed_app_candidates": ["메모장"],
            "_allowed_learned_candidates": [],
            "actions": [{"action": "open_app", "target": "계산기"}],
        }
        response, actions, issues = parser._validate_llm_result(result)
        self.assertEqual([], actions)
        self.assertTrue(any("후보에 없던 앱" in issue for issue in issues))

    def test_parser_rejects_macro_outside_ai_candidates(self):
        parser = CommandParser()
        parser.dict_mgr.learned_macros = {
            "엑셀": {"노란색": {"code": "print('ok')"}}
        }
        result = {
            "response": "실행합니다.",
            "_allowed_app_candidates": [],
            "_allowed_learned_candidates": [],
            "actions": [{
                "action": "use_learned_macro", "app_name": "엑셀", "macro_name": "노란색"
            }],
        }
        response, actions, issues = parser._validate_llm_result(result)
        self.assertEqual([], actions)
        self.assertTrue(any("후보에 없던 학습 매크로" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
