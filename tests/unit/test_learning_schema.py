"""Unit tests for bounded AI learning metadata normalization."""

import unittest

from engine.learning_schema import (
    infer_verbs_from_utterances,
    literal_utterances,
    normalize_learning_metadata,
    suggest_trigger_from_description,
)


class LearningSchemaTests(unittest.TestCase):
    def test_invalid_and_duplicate_values_are_normalized(self):
        learning = normalize_learning_metadata(
            {
                "learning": {
                    "intent": " set cell-color! ",
                    "verbs": ["칠해", "칠해", 123, " 색칠해 "],
                    "nouns": [
                        {"text": " 엑셀 ", "canonical": "엑셀", "type": "app"},
                        {"text": "노랑", "canonical": "", "type": "unsupported"},
                    ],
                    "utterances": ["엑셀 A1을 노랗게", "{app} {cell}을 {color}로"],
                    "slots": [
                        {"name": "cell", "type": "cell", "value": "A1", "required": True},
                        {"name": "cell", "type": "text", "value": "B2", "required": False},
                    ],
                }
            },
            "엑셀 A1을 노랗게 칠해",
        )

        self.assertEqual("SET_CELL_COLOR", learning["intent"])
        self.assertEqual(["칠해", "색칠해"], learning["verbs"])
        self.assertEqual("general", learning["nouns"][1]["type"])
        self.assertEqual(1, len(learning["slots"]))
        self.assertEqual("엑셀 a1을 노랗게 칠해", learning["utterances"][0])

    def test_templates_are_stored_but_not_used_as_literal_synonyms(self):
        learning = normalize_learning_metadata({
            "learning": {
                "utterances": ["창을 오른쪽으로 옮겨", "{app}을 {direction}으로 옮겨"]
            }
        })
        self.assertEqual(["창을 오른쪽으로 옮겨"], literal_utterances(learning))

    def test_description_and_user_sentence_fill_missing_learning_metadata(self):
        from_description = normalize_learning_metadata({
            "description": "오늘 날짜를 메시지 박스로 보여줍니다.",
            "learning": {},
        })
        from_user = normalize_learning_metadata(
            {"description": "창 위치 변경", "learning": {}},
            "창을 오른쪽으로 정리해줘",
        )

        self.assertEqual(
            "오늘 날짜를 메시지 박스로 보여줘",
            suggest_trigger_from_description("오늘 날짜를 메시지 박스로 보여줍니다."),
        )
        self.assertEqual(
            ["오늘 날짜를 메시지 박스로 보여줘"],
            from_description["utterances"],
        )
        self.assertEqual(["보여줘"], from_description["verbs"])
        self.assertEqual(["정리해줘"], from_user["verbs"])
        self.assertEqual(["알려줘"], infer_verbs_from_utterances(["날짜 알려 줘"]))


if __name__ == "__main__":
    unittest.main()
