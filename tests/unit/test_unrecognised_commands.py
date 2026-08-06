"""Keeping the sentences Jarvis did not understand.

The incident log counts failures but stores only a hash of what was typed,
so the wording — the thing needed to fix the vocabulary — was lost. Every
rule bug found so far was found because a person could say the sentence out
loud. These pin what is kept, what is not, and what never gets recorded.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.diagnostics.unrecognised_commands import (
    MAX_LENGTH,
    UnrecognisedCommandLog,
    record_refusal,
)


class RecordingTests(unittest.TestCase):
    def _log(self, temp_dir):
        return UnrecognisedCommandLog(Path(temp_dir) / "missed.json")

    def test_a_refused_wording_is_kept_with_its_reason(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            log = self._log(temp_dir)
            log.record("각주 모양 바꾸고 싶어", "hwp", "지원하는 한글 편집 예", "2026-08-06")

            entry = log.entries()[0]
            self.assertEqual("각주 모양 바꾸고 싶어", entry["command"])
            self.assertEqual("hwp", entry["app_type"])
            self.assertEqual(1, entry["count"])

    def test_the_same_wording_counts_up_rather_than_repeating(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            log = self._log(temp_dir)
            for _ in range(3):
                log.record("각주 모양 바꿔", "hwp", "", "2026-08-06")

            self.assertEqual(1, len(log.entries()))
            self.assertEqual(3, log.entries()[0]["count"])

    def test_the_most_repeated_wording_comes_first(self):
        # The wording worth fixing is the one said again and again.
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            log = self._log(temp_dir)
            log.record("한 번만 한 말", "hwp", "", "2026-08-06")
            for _ in range(4):
                log.record("자주 하는 말", "hwp", "", "2026-08-06")

            self.assertEqual("자주 하는 말", log.entries()[0]["command"])

    def test_a_sentence_long_enough_to_be_a_document_is_not_kept(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            log = self._log(temp_dir)
            self.assertFalse(log.record("가" * (MAX_LENGTH + 1), "hwp", "", ""))

    def test_the_reader_can_empty_the_list_and_drop_one_line(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            log = self._log(temp_dir)
            log.record("하나", "hwp", "", "")
            log.record("둘", "hwp", "", "")

            self.assertTrue(log.forget("하나", "hwp"))
            self.assertEqual(1, len(log.entries()))
            self.assertEqual(1, log.clear())
            self.assertEqual((), log.entries())

    def test_a_note_survives_a_restart(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            path = Path(temp_dir) / "missed.json"
            UnrecognisedCommandLog(path).record("행간 1.5로", "hwp", "", "")

            self.assertEqual(1, len(UnrecognisedCommandLog(path).entries()))


class OnlyRefusalsAreRecordedTests(unittest.TestCase):
    """A cancelled or busy request is not a wording Jarvis failed to read."""

    def test_only_a_blocked_result_is_recorded(self):
        cases = {
            "blocked": True,
            "cancelled": False,
            "busy": False,
            "confirmation_required": False,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                result = {"success": False, "status": status, "message": ""}
                self.assertEqual(
                    expected, self._records(result, "무슨 말")
                )

    def test_a_successful_edit_is_not_recorded(self):
        self.assertFalse(
            self._records({"success": True, "status": "success"}, "굵게 해줘")
        )

    def test_an_empty_command_is_not_recorded(self):
        self.assertFalse(
            self._records({"success": False, "status": "blocked"}, "   ")
        )

    @staticmethod
    def _records(result, command):
        import tempfile

        from engine.diagnostics import unrecognised_commands as module

        with tempfile.TemporaryDirectory(prefix="jarvis-missed-") as temp_dir:
            previous = module._SHARED
            module._SHARED = UnrecognisedCommandLog(Path(temp_dir) / "m.json")
            try:
                return record_refusal(result, command, "hwp")
            finally:
                module._SHARED = previous


if __name__ == "__main__":
    unittest.main()
