"""Regression tests for persistent learned-macro maintenance and usage stats."""

import os
import tempfile
import unittest

from engine.managers.dict_manager import DictionaryManager


def _seed_learned(manager, name="창_이동"):
    learned = {
        "description": "창을 이동",
        "code": "",
        "plan": [{"action": "wait", "seconds": 0.1}],
        "utterances": ["창 이동"],
        "learning": {
            "intent": "MOVE_WINDOW",
            "argument_mode": "json",
            "verbs": ["옮겨"],
            "nouns": [],
            "utterances": ["창 이동", "{app}을 {direction}으로 옮겨"],
            "slots": [
                {
                    "name": "direction", "type": "direction",
                    "value": "오른쪽", "required": True,
                }
            ],
        },
        "verification_status": "verified",
        "usage_count": 2,
        "success_count": 2,
        "failure_count": 0,
    }
    manager.learned_macros = {"시스템": {name: learned}}
    manager.macro_dict[name] = {
        "name": name,
        "type": "learned",
        "app": "시스템",
        "synonyms": ["창 이동"],
    }
    manager.save()


class LearningLibraryTests(unittest.TestCase):
    def test_records_hide_code_and_include_usage_summary(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-library-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionaries.json"))
            _seed_learned(manager)
            manager.learned_macros["시스템"]["창_이동"]["code"] = "print('hidden')"

            records = manager.get_learned_macro_records()

        self.assertEqual(1, len(records))
        self.assertEqual(2, records[0]["usage_count"])
        self.assertEqual("action_plan", records[0]["kind"])
        self.assertNotIn("code", records[0])
        self.assertNotIn("plan", records[0])

    def test_rename_updates_both_indexes_and_preserves_executable_plan(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-library-test-") as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            manager = DictionaryManager(path)
            _seed_learned(manager)

            result = manager.update_learned_macro("시스템", "창_이동", {
                "name": "창 정돈!",
                "utterances": ["창 정돈", "{app}을 {direction}으로 정돈해"],
                "verbs": ["정돈해", "정돈해"],
            })
            reloaded = DictionaryManager(path)

        self.assertEqual("창_정돈", result["name"])
        self.assertNotIn("창_이동", reloaded.macro_dict)
        self.assertNotIn("창_이동", reloaded.learned_macros["시스템"])
        saved = reloaded.learned_macros["시스템"]["창_정돈"]
        self.assertTrue(saved["plan"])
        self.assertEqual(2, saved["usage_count"])
        self.assertEqual(["정돈해"], saved["learning"]["verbs"])
        self.assertEqual(["창 정돈"], saved["utterances"])
        self.assertEqual(["창 정돈"], reloaded.macro_dict["창_정돈"]["synonyms"])

    def test_invalid_or_colliding_edit_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-library-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionaries.json"))
            _seed_learned(manager)
            manager.macro_dict["이미_있음"] = {"type": "cmd", "synonyms": ["기존"]}

            with self.assertRaisesRegex(ValueError, "이미 사용"):
                manager.update_learned_macro("시스템", "창_이동", {
                    "name": "이미 있음",
                    "utterances": ["바뀐 문장"],
                    "verbs": ["바꿔"],
                })

        self.assertIn("창_이동", manager.learned_macros["시스템"])
        self.assertEqual(
            ["옮겨"], manager.learned_macros["시스템"]["창_이동"]["learning"]["verbs"]
        )

    def test_delete_removes_source_and_action_link_after_reload(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-library-test-") as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            manager = DictionaryManager(path)
            _seed_learned(manager)

            self.assertTrue(manager.delete_learned_macro("시스템", "창_이동"))
            reloaded = DictionaryManager(path)

        self.assertNotIn("창_이동", reloaded.macro_dict)
        self.assertNotIn("시스템", reloaded.learned_macros)
        self.assertFalse(manager.delete_learned_macro("시스템", "창_이동"))

    def test_usage_results_accumulate_and_persist(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-library-test-") as temp_dir:
            path = os.path.join(temp_dir, "dictionaries.json")
            manager = DictionaryManager(path)
            _seed_learned(manager)

            manager.record_learned_macro_result("시스템", "창_이동", True)
            manager.record_learned_macro_result("시스템", "창_이동", False)
            reloaded = DictionaryManager(path)

        saved = reloaded.learned_macros["시스템"]["창_이동"]
        self.assertEqual(4, saved["usage_count"])
        self.assertEqual(3, saved["success_count"])
        self.assertEqual(1, saved["failure_count"])
        self.assertEqual("failed", saved["last_status"])
        self.assertTrue(saved["last_used_at"])


if __name__ == "__main__":
    unittest.main()
