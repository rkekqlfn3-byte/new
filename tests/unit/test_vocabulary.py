"""Shared Korean wording must mean the same thing on every route.

The audit found `좌측 정렬` working through the Excel command path but silently
unrecognised in edit mode, and `하지마` cancelling an Excel clarification but
not the PDF external-transfer confirmation.  Same sentence, different answer
depending on which code path it took.
"""

from __future__ import annotations

import re
import unittest

from engine.app_actions.base import AppActionBlocked
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.operations.hwp.state import (
    normalize_alignment as hwp_alignment,
)
from engine.app_actions.operations.powerpoint.state import (
    normalize_alignment as ppt_alignment,
)
from engine.app_actions.operations.word.state import (
    normalize_alignment as word_alignment,
)
from engine.managers.pending_confirmation_manager import PendingConfirmationManager
from engine.vocabulary.alignment import (
    ALIGNMENT_COMMAND_PATTERN,
    CANONICAL_ALIGNMENTS,
    korean_labels,
    normalize_alignment,
)
from engine.vocabulary.confirmation import CANCEL_ALIASES

SPOKEN_LEFT = ("왼쪽", "왼쪽 정렬", "좌측", "좌측 정렬")
SPOKEN_JUSTIFY = ("양쪽", "양쪽 정렬", "배분", "배분 정렬")


class AlignmentVocabularyTests(unittest.TestCase):
    def test_every_spoken_form_resolves_to_one_canonical_name(self):
        for spoken in SPOKEN_LEFT:
            with self.subTest(spoken=spoken):
                self.assertEqual("left", normalize_alignment(spoken))
        for spoken in SPOKEN_JUSTIFY:
            with self.subTest(spoken=spoken):
                self.assertEqual("justify", normalize_alignment(spoken))
        self.assertEqual("center", normalize_alignment("중앙"))
        self.assertEqual("right", normalize_alignment("우측"))

    def test_edit_mode_recognises_the_forms_it_used_to_drop(self):
        # These were the regression: matched by the command parser, invisible
        # to the edit-mode intent analysers.
        for command in (
            "좌측 정렬해줘",
            "우측으로 정렬",
            "배분 정렬해줘",
            "왼쪽 정렬해줘",
            "중앙 정렬",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(re.search(ALIGNMENT_COMMAND_PATTERN, command))

    def test_all_four_adapters_accept_the_same_wording(self):
        for spoken in SPOKEN_LEFT:
            with self.subTest(spoken=spoken):
                self.assertEqual("left", hwp_alignment(spoken))
                self.assertEqual("left", word_alignment(spoken)[0])
                self.assertEqual("left", ppt_alignment(spoken)[0])
                self.assertEqual("left", ExcelAdapter._normalize_alignment(spoken)[0])

    def test_an_app_that_cannot_justify_says_so_instead_of_going_quiet(self):
        # Excel has no justify. The old behaviour was that `양쪽 정렬` simply
        # did not parse, which tells the user nothing.
        for spoken in SPOKEN_JUSTIFY:
            with self.subTest(spoken=spoken):
                self.assertEqual("justify", hwp_alignment(spoken))
                with self.assertRaises(AppActionBlocked) as caught:
                    ExcelAdapter._normalize_alignment(spoken)
                self.assertIn("왼쪽", str(caught.exception))

    def test_unknown_wording_is_still_rejected(self):
        for spoken in ("비스듬히", "", None, "대각선 정렬"):
            with self.subTest(spoken=spoken):
                with self.assertRaises(ValueError):
                    normalize_alignment(spoken)

    def test_labels_cover_every_canonical_alignment(self):
        labels = korean_labels()
        self.assertEqual(set(CANONICAL_ALIGNMENTS), set(labels))
        self.assertEqual("왼쪽", labels["left"])
        self.assertEqual("양쪽 정렬", korean_labels(" 정렬")["justify"])


class CancelVocabularyTests(unittest.TestCase):
    def _pending(self, aliases):
        manager = PendingConfirmationManager()
        manager.create(
            session_id="session",
            execution_id="execution",
            original_command="명령",
            reason="pdf_external_ai",
            request_kind="confirmation",
            message="진행할까요?",
            action="pdf_analysis",
            target=None,
            options=[
                {
                    "id": "continue",
                    "label": "전송하고 실행",
                    "aliases": ["응", "네", "예", "진행", "계속", "실행"],
                    "recommended": True,
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "cancel": True,
                    "aliases": list(aliases),
                },
            ],
        )
        return manager

    def test_every_way_to_say_no_reaches_a_cancel_option(self):
        # The option author listed four; the manager merges the rest, so the
        # PDF confirmation can no longer be the one that ignores `하지마`.
        manager = self._pending(["아니", "아니요", "그만", "취소"])
        for word in sorted(CANCEL_ALIASES):
            with self.subTest(word=word):
                self.assertEqual("cancel", manager.resolve_text("session", word))

    def test_a_cancel_option_declaring_no_aliases_still_accepts_them(self):
        manager = self._pending([])
        for word in ("하지마", "아니오", "ㄴㄴ", "버려"):
            with self.subTest(word=word):
                self.assertEqual("cancel", manager.resolve_text("session", word))

    def test_approval_wording_is_untouched(self):
        manager = self._pending(["아니", "아니요"])
        for word in ("응", "네", "예", "계속", "실행"):
            with self.subTest(word=word):
                self.assertEqual("continue", manager.resolve_text("session", word))

    def test_non_cancel_options_do_not_absorb_cancel_wording(self):
        manager = PendingConfirmationManager()
        manager.create(
            session_id="session",
            execution_id="execution",
            original_command="명령",
            reason="multiple_possible_intents",
            request_kind="clarification",
            message="어느 쪽인가요?",
            action="edit",
            target=None,
            options=[
                {"id": "first", "label": "첫째"},
                {"id": "second", "label": "둘째"},
            ],
        )
        self.assertIsNone(manager.resolve_text("session", "하지마"))


if __name__ == "__main__":
    unittest.main()
