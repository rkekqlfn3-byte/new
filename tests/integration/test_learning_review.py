"""Tests for reviewing and editing pending macro learning candidates."""

import os
import tempfile
import unittest

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


def _pending_candidate(name="창_배치"):
    return {
        "app": "시스템",
        "name": name,
        "desc": "창을 지정 위치에 배치",
        "target": "",
        "code": "",
        "plan": [{"action": "wait", "seconds": 0.1}],
        "steps": [{"step": "창 위치 확인"}],
        "utterances": ["{app}을 {direction}으로 옮겨"],
        "learning": {
            "intent": "MOVE_WINDOW",
            "argument_mode": "json",
            "verbs": ["옮겨"],
            "nouns": [
                {"text": "계산기앱", "canonical": "계산기", "type": "app"}
            ],
            "utterances": ["{app}을 {direction}으로 옮겨"],
            "slots": [
                {
                    "name": "direction",
                    "type": "direction",
                    "value": "오른쪽",
                    "required": True,
                }
            ],
        },
        "verification_status": "confirmation_required",
        "verification": [{"step": 1, "status": "confirmation_required"}],
    }


class LearningReviewTests(unittest.TestCase):
    def _parser(self, path):
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(path)
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser.action_executor)
        return parser

    def test_review_exposes_structure_but_not_dynamic_code(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            candidate = _pending_candidate()
            candidate["code"] = "print('must not be exposed')"
            parser.pending_macros = [candidate]

            review = parser.get_pending_learning_review()

        self.assertTrue(review["pending"])
        self.assertEqual("action_plan", review["candidates"][0]["kind"])
        self.assertEqual("MOVE_WINDOW", review["candidates"][0]["intent"])
        self.assertNotIn("code", review["candidates"][0])

    def test_edited_candidate_is_normalized_saved_and_persisted(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            parser = self._parser(path)
            parser.pending_macros = [_pending_candidate()]

            response = parser.approve_pending_learning([{
                "index": 0,
                "name": "창 배치 새 이름!",
                "utterances": ["창 정돈", "{app}을 {direction}으로 정돈해"],
                "verbs": ["정돈해", "정돈해"],
            }])

            saved = parser.dict_mgr.learned_macros["시스템"]["창_배치_새_이름"]
            reloaded = DictionaryManager(path)

        self.assertIn("저장", response)
        self.assertFalse(parser.pending_macros)
        self.assertEqual(["정돈해"], saved["learning"]["verbs"])
        self.assertEqual("user_confirmed", saved["verification_status"])
        self.assertEqual(["창 정돈"], saved["utterances"])
        self.assertIn(
            "창_배치_새_이름", reloaded.learned_macros["시스템"]
        )

    def test_invalid_multi_edit_does_not_partially_change_candidates(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.pending_macros = [
                _pending_candidate("첫_후보"), _pending_candidate("둘째_후보")
            ]

            with self.assertRaisesRegex(ValueError, "발동 문장"):
                parser.approve_pending_learning([
                    {
                        "index": 0,
                        "name": "수정된 이름",
                        "utterances": ["수정 문장"],
                        "verbs": ["수정해"],
                    },
                    {
                        "index": 1,
                        "name": "둘째 후보",
                        "utterances": [],
                        "verbs": ["옮겨"],
                    },
                ])

        self.assertEqual("첫_후보", parser.pending_macros[0]["name"])
        self.assertEqual(["옮겨"], parser.pending_macros[0]["learning"]["verbs"])
        self.assertEqual(2, len(parser.pending_macros))

    def test_name_collision_gets_stable_suffix(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.pending_macros = [
                _pending_candidate("수정_대상"), _pending_candidate("보존_이름")
            ]
            parser.approve_pending_learning([{
                "index": 0,
                "name": "보존 이름",
                "utterances": ["첫 명령"],
                "verbs": ["실행해"],
            }])

        names = parser.dict_mgr.learned_macros["시스템"]
        self.assertIn("보존_이름", names)
        self.assertIn("보존_이름_2", names)

    def test_approval_infers_missing_verbs_before_saving_active_macro(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            candidate = _pending_candidate("날짜_표시")
            candidate["learning"]["utterances"] = ["오늘 날짜 알려 줘"]
            candidate["learning"]["verbs"] = []
            parser.pending_macros = [candidate]

            parser.approve_pending_learning()
            saved = parser.dict_mgr.learned_macros["시스템"]["날짜_표시"]

        self.assertEqual("active", saved["state"])
        self.assertEqual(["알려줘"], saved["learning"]["verbs"])
        self.assertEqual(["오늘 날짜 알려 줘"], saved["utterances"])

    def test_unrelated_chat_waits_and_explicit_discard_clears(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.pending_macros = [_pending_candidate()]

            waiting = parser.parse_and_execute("계산기 다시 열어")
            discarded = parser.reject_pending_learning("run_once")

        self.assertIn("검토", waiting)
        self.assertIn("이번 한 번", discarded)
        self.assertFalse(parser.pending_macros)
        self.assertFalse(parser.dict_mgr.learned_macros)

    def test_common_korean_rejection_word_does_not_save(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-review-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.pending_macros = [_pending_candidate()]

            response = parser.parse_and_execute("아니요")

        self.assertIn("폐기", response)
        self.assertFalse(parser.pending_macros)
        self.assertFalse(parser.dict_mgr.learned_macros)


if __name__ == "__main__":
    unittest.main()
