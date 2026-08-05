"""The provider fallback for PDF commands the patterns did not match.

``parse_pdf_intent`` matches a fixed list of wordings, so 겹쳐줘 fails where
합쳐줘 works even though both ask to merge. These pin what the provider may
turn that sentence into, and what the intent's own validation still refuses.
"""

from __future__ import annotations

import json
import unittest

from engine.pdf.intake import PdfIntakeManager
from engine.pdf.intent import PdfIntentError, PdfIntentKind
from engine.pdf.llm_intent import (
    PDF_INTENT_GUIDE,
    PdfIntentTranslator,
    build_prompt,
)


class FakeCaller:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, text):
        self.calls.append((prompt, text))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def reply(kind, **fields):
    body = {"kind": kind, "query": None, "output_kinds": [], "rotation_degrees": None}
    body.update(fields)
    return json.dumps(
        {"response": json.dumps(body, ensure_ascii=False)}, ensure_ascii=False
    )


class GuideTests(unittest.TestCase):
    def test_every_named_kind_is_a_real_intent(self):
        for kind in PDF_INTENT_GUIDE:
            with self.subTest(kind=kind):
                self.assertIsInstance(PdfIntentKind(kind), PdfIntentKind)

    def test_every_intent_is_offered(self):
        self.assertEqual(
            set(), {kind.value for kind in PdfIntentKind} - set(PDF_INTENT_GUIDE)
        )

    def test_the_prompt_names_the_closed_list(self):
        prompt = build_prompt()
        self.assertIn("merge", prompt)
        self.assertIn("unsupported", prompt)


class TranslatorTests(unittest.TestCase):
    def _translate(self, raw, command="두 개 겹쳐줘"):
        return PdfIntentTranslator(FakeCaller(raw)).translate(command)

    def test_an_unrecognised_wording_becomes_a_real_intent(self):
        intent = self._translate(reply("merge"))
        self.assertIs(PdfIntentKind.MERGE, intent.kind)

    def test_a_search_carries_its_query(self):
        intent = self._translate(reply("search", query="보증"))
        self.assertIs(PdfIntentKind.SEARCH, intent.kind)
        self.assertEqual("보증", intent.query)

    def test_a_search_without_a_query_is_refused(self):
        # The intent requires one, so a translation missing it is discarded
        # rather than passed on to fail later.
        self.assertIsNone(self._translate(reply("search")))

    def test_a_rotation_that_is_not_a_quarter_turn_is_refused(self):
        self.assertIsNone(self._translate(reply("rotate", rotation_degrees=45)))
        self.assertIsNotNone(self._translate(reply("rotate", rotation_degrees=90)))

    def test_an_output_kind_that_intent_cannot_produce_is_refused(self):
        self.assertIsNone(self._translate(reply("summary", output_kinds=["excel"])))
        self.assertIsNotNone(self._translate(reply("report", output_kinds=["hwp"])))

    def test_an_invented_kind_is_refused(self):
        self.assertIsNone(self._translate(reply("delete_everything")))

    def test_unsupported_is_refused_rather_than_guessed_at(self):
        self.assertIsNone(self._translate(reply("unsupported")))

    def test_an_unusable_reply_is_not_an_error_for_the_caller(self):
        for raw in ("", "모르겠습니다", "{", None):
            self.assertIsNone(self._translate(raw))

    def test_a_provider_outage_is_not_an_error_for_the_caller(self):
        self.assertIsNone(self._translate(OSError("no network")))


class IntakeWiringTests(unittest.TestCase):
    def test_a_wording_the_patterns_know_never_reaches_the_provider(self):
        caller = FakeCaller(reply("split"))
        manager = PdfIntakeManager(intent_translator=PdfIntentTranslator(caller))
        self.assertIs(PdfIntentKind.MERGE, manager.parse_intent("합쳐줘").kind)
        self.assertEqual([], caller.calls)

    def test_a_wording_the_patterns_reject_is_translated(self):
        caller = FakeCaller(reply("merge"))
        manager = PdfIntakeManager(intent_translator=PdfIntentTranslator(caller))
        self.assertIs(PdfIntentKind.MERGE, manager.parse_intent("두 개 겹쳐줘").kind)
        self.assertEqual(1, len(caller.calls))

    def test_a_refused_translation_keeps_the_original_guidance(self):
        manager = PdfIntakeManager(
            intent_translator=PdfIntentTranslator(FakeCaller(reply("unsupported")))
        )
        with self.assertRaises(PdfIntentError):
            manager.parse_intent("두 개 겹쳐줘")

    def test_without_a_provider_nothing_changes(self):
        manager = PdfIntakeManager()
        self.assertIs(PdfIntentKind.MERGE, manager.parse_intent("합쳐줘").kind)
        with self.assertRaises(PdfIntentError):
            manager.parse_intent("두 개 겹쳐줘")


if __name__ == "__main__":
    unittest.main()
