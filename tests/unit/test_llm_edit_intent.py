"""The provider fallback for 한글 edit commands the rules did not match.

The rules only match wordings written down in advance, so ``번호 매겨줘``
failed even though 한글 numbering works. These pin what the provider is
allowed to do with that sentence — and, more importantly, what it is not.
"""

from __future__ import annotations

import json
import unittest

from engine.app_actions.operations.hwp import HWP_OPERATIONS
from engine.edit_mode.llm_intent import (
    HWP_OPERATION_GUIDE,
    LlmAssistedEditIntentAnalyzer,
    LlmEditIntentTranslator,
    build_prompt,
)
from engine.edit_mode.stage5 import Stage5EditError, StructuredEditIntentAnalyzer

HWP = {
    "app_type": "hwp",
    "selection_kind": "text",
    "selection_reference": "",
    "selected_text_preview": "안녕하세요",
}


class FakeCaller:
    """Stands in for the provider; records what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, text):
        self.calls.append((prompt, text))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def reply(operation, params=None, description="설명"):
    body = json.dumps(
        {
            "operation": operation,
            "params": params or {},
            "description": description,
        },
        ensure_ascii=False,
    )
    # The providers answer as {"response": "<json string>"}.
    return json.dumps({"response": body}, ensure_ascii=False)


class GuideIntegrityTests(unittest.TestCase):
    def test_every_named_operation_really_exists(self):
        # A guide entry the adapter cannot run would be offered to the model
        # and then refused after translation.
        self.assertEqual(
            set(), set(HWP_OPERATION_GUIDE) - set(HWP_OPERATIONS.names)
        )

    def test_the_prompt_only_offers_operations_the_adapter_allows(self):
        prompt = build_prompt({"set_line_spacing", "insert_table"})
        self.assertIn("set_line_spacing", prompt)
        self.assertNotIn("find_replace", prompt)


class TranslatorTests(unittest.TestCase):
    def _translate(self, raw, text="행간 1.5로"):
        caller = FakeCaller(raw)
        translator = LlmEditIntentTranslator(caller)
        return translator.translate(text, set(HWP_OPERATION_GUIDE)), caller

    def test_an_unrecognised_wording_becomes_a_real_operation(self):
        result, _ = self._translate(reply("set_line_spacing", {"line_spacing": 150}))
        self.assertEqual("set_line_spacing", result.operation)
        self.assertEqual({"line_spacing": 150}, result.params)

    def test_an_invented_operation_is_refused(self):
        result, _ = self._translate(reply("set_page_number"))
        self.assertIsNone(result)

    def test_unsupported_is_refused_rather_than_guessed_at(self):
        result, _ = self._translate(reply("unsupported"))
        self.assertIsNone(result)

    def test_a_parameter_the_operation_does_not_take_is_refused(self):
        # Accepting it would hand the adapter a field it never validates.
        result, _ = self._translate(
            reply("set_line_spacing", {"line_spacing": 150, "delete_everything": True})
        )
        self.assertIsNone(result)

    def test_an_operation_outside_the_allowed_set_is_refused(self):
        translator = LlmEditIntentTranslator(FakeCaller(reply("insert_table")))
        self.assertIsNone(translator.translate("표 3x4", {"set_line_spacing"}))

    def test_an_unusable_reply_is_not_an_error_for_the_caller(self):
        for raw in ("", "무슨 소리인지 모르겠습니다", "{", None):
            self.assertIsNone(self._translate(raw)[0])

    def test_a_provider_outage_is_not_an_error_for_the_caller(self):
        result, _ = self._translate(OSError("no network"))
        self.assertIsNone(result)

    def test_an_empty_command_never_reaches_the_provider(self):
        translator = LlmEditIntentTranslator(FakeCaller(reply("delete_text")))
        self.assertIsNone(translator.translate("   ", set(HWP_OPERATION_GUIDE)))


class AssistedAnalyzerTests(unittest.TestCase):
    def _analyzer(self, raw):
        caller = FakeCaller(raw)
        analyzer = LlmAssistedEditIntentAnalyzer(
            StructuredEditIntentAnalyzer(),
            LlmEditIntentTranslator(caller),
            supported_operations=set(HWP_OPERATION_GUIDE),
        )
        return analyzer, caller

    def test_a_wording_the_rules_know_never_reaches_the_provider(self):
        # The common sentences must stay instant, free and offline.
        analyzer, caller = self._analyzer(reply("delete_text"))
        intent = analyzer.analyze("굵게 해줘", HWP)
        self.assertEqual("set_text_format", intent.operation)
        self.assertEqual([], caller.calls)

    def test_a_wording_the_rules_reject_is_translated(self):
        analyzer, caller = self._analyzer(
            reply("set_list_format", {"list_format": "number"}, "번호 목록으로 바꿉니다")
        )
        intent = analyzer.analyze("번호 매겨줘", HWP)
        self.assertEqual("set_list_format", intent.operation)
        self.assertEqual({"list_format": "number"}, intent.params)
        self.assertEqual("번호 목록으로 바꿉니다", intent.description)
        self.assertEqual(1, len(caller.calls))

    def test_a_refused_translation_keeps_the_original_guidance(self):
        # The reader should see the same help as before, not a provider error.
        analyzer, _ = self._analyzer(reply("unsupported"))
        with self.assertRaises(Stage5EditError) as caught:
            analyzer.analyze("각주 달아줘", HWP)
        self.assertIn("지원하는 한글 편집 예", str(caught.exception))

    def test_apps_that_were_not_measured_are_left_to_the_rules(self):
        analyzer, caller = self._analyzer(reply("set_line_spacing"))
        with self.assertRaises(Stage5EditError):
            analyzer.analyze("두껍게 해줘", {**HWP, "app_type": "excel"})
        self.assertEqual([], caller.calls)

    def test_without_a_provider_nothing_changes(self):
        analyzer = LlmAssistedEditIntentAnalyzer(
            StructuredEditIntentAnalyzer(), None
        )
        self.assertEqual(
            "set_text_format", analyzer.analyze("굵게 해줘", HWP).operation
        )
        with self.assertRaises(Stage5EditError):
            analyzer.analyze("두껍게 해줘", HWP)


if __name__ == "__main__":
    unittest.main()
