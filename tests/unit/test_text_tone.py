import unittest

from engine.edit_mode.text_tone import classify_text_tone


class TextToneClassifierTests(unittest.TestCase):
    def test_formal_endings_are_labeled_formal(self):
        cases = (
            "검토 결과를 보고드립니다.",
            "확인해 주시겠습니까? 회신 부탁드립니다.",
            "다음 회의에 참석해 주십시오.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual("formal", classify_text_tone(text))

    def test_friendly_endings_are_labeled_friendly(self):
        cases = (
            "검토 결과를 알려드려요.",
            "이렇게 하면 될 것 같아요! 확인해 주세요.",
            "내일까지 끝낼 수 있죠?",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual("friendly", classify_text_tone(text))

    def test_plain_and_nominal_endings_are_labeled_plain(self):
        cases = (
            "매출이 증가했다. 원인은 단가 인상이다.",
            "3분기 목표 초과 달성함. 재고 부담은 감소됨.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual("plain", classify_text_tone(text))

    def test_even_split_is_mixed_and_dominant_style_wins(self):
        self.assertEqual(
            "mixed",
            classify_text_tone("보고드립니다. 확인해 주세요."),
        )
        self.assertEqual(
            "formal",
            classify_text_tone(
                "보고드립니다. 검토했습니다. 확정됐습니다. 확인해 주세요."
            ),
        )

    def test_short_or_non_korean_text_is_unknown(self):
        for text in ("", "  ", "네", "Quarterly revenue grew 12%"):
            with self.subTest(text=text):
                self.assertEqual("unknown", classify_text_tone(text))

    def test_trailing_quotes_and_spaces_do_not_hide_the_ending(self):
        self.assertEqual("formal", classify_text_tone("“검토를 완료했습니다.”  "))
        self.assertEqual("friendly", classify_text_tone("확인 부탁해요~"))
