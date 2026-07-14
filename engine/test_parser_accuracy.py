import unittest
from unittest.mock import patch

from engine.parser import CommandParser


class ParserAccuracyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = CommandParser()
        # Accuracy expectations must not depend on whichever applications are
        # present in the developer's persisted user dictionary.
        cls.parser.dict_mgr.noun_dict.update({
            "메모장": "notepad.exe",
            "계산기": "calc.exe",
            "카톡": "kakaotalk.exe",
            "파워포인트": "powerpnt.exe",
        })
        cls.parser.dict_mgr.noun_revision += 1

    def test_search_query_preserves_complete_korean_nouns(self):
        opened = []
        with patch("engine.builtins.os.startfile", side_effect=opened.append):
            result = self.parser.parse_and_execute("유튜브에 고양이 찾아줘")

        self.assertIn("'고양이'", result)
        self.assertTrue(opened[-1].endswith("%EA%B3%A0%EC%96%91%EC%9D%B4"))

    def test_search_query_preserves_spaces(self):
        with patch("engine.builtins.os.startfile"):
            result = self.parser.parse_and_execute("네이버에서 오늘 서울 날씨 검색해")

        self.assertIn("'오늘 서울 날씨'", result)

    def test_search_query_removes_alternative_search_verb(self):
        with patch("engine.builtins.os.startfile"):
            result = self.parser.parse_and_execute("구글에서 최신 파이썬 버전 알아봐")

        self.assertIn("'최신 파이썬 버전'", result)

    def test_search_verb_can_be_omitted_after_engine(self):
        with patch("engine.builtins.os.startfile"):
            result = self.parser.parse_and_execute("구글에서 파이썬")

        self.assertIn("'파이썬'", result)

    def test_common_app_typos_are_corrected(self):
        cases = [
            ("메무장 켜줘", "메모장"),
            ("메무장을 열어줘", "메모장"),
            ("계산거 열어줘", "계산기"),
            ("파워포안트 켜줘", "파워포인트"),
        ]

        for text, expected in cases:
            with self.subTest(text=text):
                macro = self.parser._identify_macro(text)
                tokens = self.parser.normalize_text(text)
                noun, _ = self.parser._identify_app(text, tokens, None, macro)
                self.assertEqual(expected, noun)

    def test_ambiguous_two_syllable_typo_is_rejected(self):
        text = "카툭 열어줘"
        macro = self.parser._identify_macro(text)
        noun, _ = self.parser._identify_app(
            text, self.parser.normalize_text(text), None, macro
        )
        self.assertIsNone(noun)

    def test_normalization_does_not_truncate_noun(self):
        self.assertIn("고양이", self.parser.normalize_text("유튜브에 고양이 찾아줘"))


if __name__ == "__main__":
    unittest.main()
