import time
import unittest

from engine.llm_engine import LLMEngine
from engine.parser import CommandParser


class ParserPerformanceTests(unittest.TestCase):
    def test_ai_action_loop_has_no_unconditional_half_second_delay(self):
        parser = CommandParser()
        parser.llm_engine.process_command = lambda *args, **kwargs: {
            "response": "ok",
            "actions": [
                {"action": "none"},
                {"action": "none"},
                {"action": "none"},
            ],
        }

        started = time.perf_counter()
        result = parser.parse_and_execute("성능 테스트용 미등록 문장")
        elapsed = time.perf_counter() - started

        self.assertEqual("ok", result)
        self.assertLess(elapsed, 0.25)

    def test_app_index_rebuilds_after_dictionary_revision(self):
        parser = CommandParser()
        parser.dict_mgr.noun_dict = {"첫앱": "first.exe"}
        parser.dict_mgr.noun_revision += 1
        self.assertEqual(
            "첫앱",
            parser._identify_app("첫앱 켜줘", ["첫앱"], None, "OPEN")[0],
        )

        parser.dict_mgr.noun_dict["둘째앱"] = "second.exe"
        parser.dict_mgr.noun_revision += 1
        self.assertEqual(
            "둘째앱",
            parser._identify_app("둘째앱 켜줘", ["둘째앱"], None, "OPEN")[0],
        )

    def test_repeated_fuzzy_lookup_uses_cache(self):
        parser = CommandParser()
        parser.dict_mgr.noun_dict = {"메모장": "notepad.exe", "계산기": "calc.exe"}
        parser.dict_mgr.noun_revision += 1

        first = parser._identify_app("메무장 켜줘", ["메무장"], None, "OPEN")
        cache_size = len(parser.local_command_analyzer._fuzzy_app_cache)
        second = parser._identify_app("메무장 켜줘", ["메무장"], None, "OPEN")

        self.assertEqual(("메모장", "notepad.exe"), first)
        self.assertEqual(first, second)
        self.assertEqual(
            cache_size, len(parser.local_command_analyzer._fuzzy_app_cache)
        )


class CommandContextCacheTests(unittest.TestCase):
    class DummyDictionary:
        def __init__(self):
            self.noun_dict = {"메모장": "notepad.exe"}
            self.search_engines_dict = {"구글": "https://google.test?q="}
            self.noun_revision = 1

        def get_nouns(self):
            return self.noun_dict

    def test_context_cache_refreshes_only_after_revision(self):
        dictionary = self.DummyDictionary()
        engine = LLMEngine(dictionary)

        first = engine._get_command_dictionary_context("메모장 열어")
        first_signature = engine.command_context_builder._app_signature
        second = engine._get_command_dictionary_context("메모장 열어")
        self.assertEqual(first, second)
        self.assertEqual(first_signature, engine.command_context_builder._app_signature)

        dictionary.noun_dict["계산기"] = "calc.exe"
        dictionary.noun_revision += 1
        refreshed = engine._get_command_dictionary_context("계산기 열어")
        self.assertIn("계산기", refreshed)
        self.assertNotEqual(first_signature, engine.command_context_builder._app_signature)


if __name__ == "__main__":
    unittest.main()
