import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from engine.managers.dict_manager import DictionaryManager


class DictionaryMemoryLockTests(unittest.TestCase):
    def _manager(self, temp_dir):
        return DictionaryManager(os.path.join(temp_dir, "dictionary.json"))

    def test_submanagers_share_dictionary_rlock(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-lock-test-") as temp_dir:
            manager = self._manager(temp_dir)
            self.assertIs(manager._lock, manager.macro_manager.lock)
            self.assertIs(manager._lock, manager.config_manager.lock)

    def test_concurrent_usage_updates_are_not_lost(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-lock-test-") as temp_dir:
            manager = self._manager(temp_dir)
            record = {
                "code": "print('ok')", "utterances": ["동시 실행"],
                "learning": {"utterances": ["동시 실행"]}, "state": "active",
            }
            manager.learned_macros = {"시스템": {"concurrent": record}}
            manager.macro_dict["concurrent"] = {
                "type": "learned", "app": "시스템", "state": "active",
                "synonyms": ["동시 실행"],
            }

            def update(_):
                manager.record_learned_macro_result(
                    "시스템", "concurrent", True
                )

            with mock.patch.object(manager, "save"):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(update, range(800)))
            self.assertEqual(800, record["usage_count"])
            self.assertEqual(800, record["success_count"])
            self.assertEqual(0, record.get("failure_count", 0))

    def test_getters_return_copies_not_mutable_internal_objects(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-lock-test-") as temp_dir:
            manager = self._manager(temp_dir)
            nouns = manager.get_nouns()
            config = manager.get_ai_config()
            favorites = manager.get_favorites()
            nouns["외부변경"] = "bad"
            config["provider"] = "bad"
            favorites.append("bad")
            self.assertNotIn("외부변경", manager.noun_dict)
            self.assertNotEqual("bad", manager.ai_config["provider"])
            self.assertNotIn("bad", manager.favorites)


if __name__ == "__main__":
    unittest.main()
