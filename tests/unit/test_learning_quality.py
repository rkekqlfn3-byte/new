import os
import tempfile
import unittest

from engine.learning_quality import (
    clean_trigger_list,
    find_trigger_conflicts,
    looks_like_internal_trigger,
)
from engine.managers.dict_manager import DictionaryManager
from engine.template_matcher import LearnedTemplateMatcher


class LearningQualityTests(unittest.TestCase):
    def test_internal_identifier_is_not_a_trigger(self):
        self.assertTrue(looks_like_internal_trigger("set_a1_yellow", "set_a1_yellow"))
        self.assertEqual([], clean_trigger_list(["set_a1_yellow"], "set_a1_yellow"))
        self.assertEqual(["a1 셀 노란색으로"], clean_trigger_list([
            "set_a1_yellow", "A1 셀 노란색으로"
        ], "set_a1_yellow"))

    def test_exact_and_near_trigger_conflicts_are_reported(self):
        learned = {"시스템": {"기존": {"learning": {
            "utterances": ["메모장을 오른쪽으로 옮겨"]
        }}}}
        exact = find_trigger_conflicts(learned, ["메모장을 오른쪽으로 옮겨"])
        near = find_trigger_conflicts(learned, ["메모장을 오른쪽으로 옮겨줘"])
        self.assertEqual("exact", exact[0]["type"])
        self.assertEqual("similar", near[0]["type"])

    def test_failure_types_change_state_without_auto_disabling(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-quality-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionary.json"))
            record = {
                "code": "print('ok')", "utterances": ["테스트 실행"],
                "learning": {"utterances": ["테스트 실행"]}, "state": "active",
            }
            manager.learned_macros = {"시스템": {"test_macro": record}}
            manager.macro_dict["test_macro"] = {
                "type": "learned", "app": "시스템", "synonyms": ["테스트 실행"]
            }
            for _ in range(3):
                manager.record_learned_macro_result(
                    "시스템", "test_macro", False, "execution_error"
                )
            self.assertEqual("needs_review", record["state"])
            self.assertNotEqual("disabled", record["state"])
            manager.set_learned_macro_state("시스템", "test_macro", "active")
            for _ in range(3):
                manager.record_learned_macro_result(
                    "시스템", "test_macro", False, "validation_error"
                )
            self.assertEqual("broken", record["state"])

    def test_verification_and_environment_failures_have_distinct_effects(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-quality-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionary.json"))
            record = {
                "code": "print('ok')", "utterances": ["테스트 실행"],
                "learning": {"utterances": ["테스트 실행"]}, "state": "active",
            }
            manager.learned_macros = {"시스템": {"test_macro": record}}
            manager.record_learned_macro_result(
                "시스템", "test_macro", False, "environment_error"
            )
            self.assertEqual("active", record["state"])
            self.assertEqual(0, record["consecutive_failures"])
            for _ in range(3):
                manager.record_learned_macro_result(
                    "시스템", "test_macro", False, "verification_error"
                )
            self.assertEqual("needs_review", record["state"])
            self.assertEqual("verification_error", record["last_failure_type"])

    def test_cancel_does_not_increment_failure_statistics(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-quality-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionary.json"))
            record = {
                "code": "print('ok')", "utterances": ["테스트 실행"],
                "learning": {"utterances": ["테스트 실행"]}, "state": "active",
            }
            manager.learned_macros = {"시스템": {"test_macro": record}}
            manager.record_learned_macro_result(
                "시스템", "test_macro", False, "cancelled"
            )
            self.assertEqual(0, record.get("usage_count", 0))
            self.assertEqual(0, record.get("failure_count", 0))
            self.assertEqual("user_cancelled", record.get("last_failure_type"))

    def test_inactive_template_is_not_matched(self):
        macros = {"시스템": {"move": {
            "state": "disabled",
            "learning": {
                "utterances": ["{app}을 {direction}으로 옮겨"],
                "slots": [
                    {"name": "app", "type": "app"},
                    {"name": "direction", "type": "direction"},
                ],
            },
        }}}
        match = LearnedTemplateMatcher().match(
            "메모장을 오른쪽으로 옮겨", macros, {"메모장": "notepad"}
        )
        self.assertIsNone(match)

    def test_macro_without_natural_trigger_cannot_be_reactivated(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-quality-test-") as temp_dir:
            manager = DictionaryManager(os.path.join(temp_dir, "dictionary.json"))
            manager.learned_macros = {"엑셀": {"set_a1_yellow": {
                "code": "print('ok')",
                "utterances": ["set_a1_yellow"],
                "state": "needs_review",
            }}}
            manager.macro_dict["set_a1_yellow"] = {
                "type": "learned", "app": "엑셀", "synonyms": [],
                "state": "needs_review",
            }
            with self.assertRaisesRegex(ValueError, "자연스러운 발동 문장"):
                manager.set_learned_macro_state(
                    "엑셀", "set_a1_yellow", "active"
                )


if __name__ == "__main__":
    unittest.main()
