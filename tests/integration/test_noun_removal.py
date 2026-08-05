"""Deleting an entry from the app list.

A scan fills the list with whatever it finds — bookmarks the user does not
want offered as apps among them — so the list is only usable if entries can
be taken out and stay out. These pin the parts that make a delete stick.
"""

import os
import tempfile
import unittest

from engine.managers.dict_manager import DictionaryManager


class NounRemovalTests(unittest.TestCase):
    def _manager(self, temp_dir):
        manager = DictionaryManager(os.path.join(temp_dir, "dictionary.json"))
        manager.noun_dict = {
            "지울 사이트": "https://example.test/one",
            "남길 사이트": "https://example.test/two",
        }
        return manager

    def test_a_deleted_entry_leaves_the_list(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            manager = self._manager(temp_dir)

            self.assertTrue(manager.remove_noun("지울 사이트"))

            self.assertNotIn("지울 사이트", manager.noun_dict)
            self.assertIn("남길 사이트", manager.noun_dict)

    def test_deleting_a_row_takes_its_other_names_with_it(self):
        # The list shows one row per target. Leaving a synonym behind brings
        # the row straight back and the delete looks like it did nothing.
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            manager = self._manager(temp_dir)
            manager.noun_dict["같은 곳 다른 이름"] = "https://example.test/one"

            manager.remove_noun("지울 사이트")

            self.assertNotIn("같은 곳 다른 이름", manager.noun_dict)

    def test_a_deleted_entry_is_not_brought_back_by_the_next_scan(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            manager = self._manager(temp_dir)
            manager.remove_noun("지울 사이트")

            # A scan finds the same bookmark again, under any name.
            manager.noun_dict["다시 찾은 이름"] = "https://example.test/one"
            self.assertEqual(1, manager._drop_removed_targets())

            self.assertEqual(
                {"남길 사이트": "https://example.test/two"}, manager.noun_dict
            )

    def test_teaching_the_entry_back_undoes_the_deletion(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            manager = self._manager(temp_dir)
            manager.remove_noun("지울 사이트")

            manager.add_custom_noun("다시 쓸래", "https://example.test/one")
            manager._drop_removed_targets()

            self.assertIn("다시 쓸래", manager.noun_dict)

    def test_deleting_a_favourite_also_clears_the_favourite(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            manager = self._manager(temp_dir)
            manager.toggle_favorite("지울 사이트")

            manager.remove_noun("지울 사이트")

            self.assertNotIn("지울 사이트", manager.favorites)

    def test_deleting_something_that_is_not_there_reports_failure(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            self.assertFalse(self._manager(temp_dir).remove_noun("없는 항목"))

    def test_the_deletion_survives_a_restart(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-noun-") as temp_dir:
            path = os.path.join(temp_dir, "dictionary.json")
            manager = DictionaryManager(path)
            manager.noun_dict = {"지울 사이트": "https://example.test/one"}
            manager.remove_noun("지울 사이트")

            reopened = DictionaryManager(path)
            reopened.load()

            self.assertIn("https://example.test/one", reopened.removed_targets)


if __name__ == "__main__":
    unittest.main()
