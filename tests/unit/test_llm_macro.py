"""Proposing a sequence for a request no single operation fits.

Layer 3 is the riskiest layer, so most of what matters here is what it
refuses: anything that would make something new executable, and anything a
reader could not check on a preview. It also never runs what it proposes.
"""

from __future__ import annotations

import json
import unittest

from engine.edit_mode.llm_intent import (
    HWP_OPERATION_GUIDE,
    LlmAssistedEditIntentAnalyzer,
    LlmEditIntentTranslator,
)
from engine.edit_mode.llm_macro import (
    MAX_STEPS,
    LlmMacroComposer,
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
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, prompt, text):
        self.calls.append(text)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def plan(*steps, summary="표를 만들고 첫 줄을 굵게"):
    body = json.dumps(
        {
            "summary": summary,
            "steps": [
                {"operation": name, "params": params, "description": name}
                for name, params in steps
            ],
        },
        ensure_ascii=False,
    )
    return json.dumps({"response": body}, ensure_ascii=False)


GOOD = (
    ("insert_table", {"rows": 2, "columns": 2}),
    ("set_table_cell", {"row": 1, "column": 1, "text": "제목"}),
)


class ComposerTests(unittest.TestCase):
    def _compose(self, raw, text="표 만들고 첫 칸에 제목 넣어줘"):
        composer = LlmMacroComposer(FakeCaller(raw))
        return composer.compose(text, set(HWP_OPERATION_GUIDE), "hwp", HWP)

    def test_a_multi_step_request_becomes_an_ordering(self):
        proposal = self._compose(plan(*GOOD))
        self.assertEqual(2, len(proposal.steps))
        self.assertEqual("insert_table", proposal.steps[0].operation)
        self.assertIn("1.", proposal.as_text())

    def test_the_prompt_asks_for_ordering_not_for_code(self):
        prompt = build_prompt({"insert_table"}, "hwp")
        self.assertIn("순서", prompt)
        self.assertIn("지어내지 말고", prompt)

    def test_an_invented_operation_is_refused(self):
        self.assertIsNone(
            self._compose(plan(("insert_table", {}), ("send_email", {})))
        )

    def test_a_parameter_the_operation_does_not_take_is_refused(self):
        self.assertIsNone(
            self._compose(
                plan(("insert_table", {"rows": 2}), ("delete_text", {"run": "rm"}))
            )
        )

    def test_a_plan_longer_than_a_reader_can_check_is_refused(self):
        self.assertIsNone(
            self._compose(plan(*([("delete_text", {})] * (MAX_STEPS + 1))))
        )

    def test_a_single_step_plan_is_refused(self):
        # One step is layer 2's job, and layer 2 already refused this.
        self.assertIsNone(self._compose(plan(("insert_table", {"rows": 2}))))

    def test_an_empty_plan_is_refused(self):
        self.assertIsNone(self._compose(plan()))

    def test_an_unusable_reply_is_not_an_error_for_the_caller(self):
        for raw in ("", "무슨 말인지 모르겠습니다", "{", None):
            self.assertIsNone(self._compose(raw))

    def test_a_provider_outage_is_not_an_error_for_the_caller(self):
        self.assertIsNone(self._compose(OSError("no network")))

    def test_an_application_without_a_guide_is_refused(self):
        composer = LlmMacroComposer(FakeCaller(plan(*GOOD)))
        self.assertIsNone(
            composer.compose("무엇이든", set(HWP_OPERATION_GUIDE), "notepad", HWP)
        )


class ProposalIsNotExecutionTests(unittest.TestCase):
    """Layer 3 suggests. A plan the reader has not seen is not approved."""

    def _analyzer(self, macro_reply, translation_reply=None):
        translation_reply = translation_reply or json.dumps(
            {"response": json.dumps({"operation": "unsupported", "params": {}})},
            ensure_ascii=False,
        )
        composer = LlmMacroComposer(FakeCaller(macro_reply))
        return LlmAssistedEditIntentAnalyzer(
            StructuredEditIntentAnalyzer(),
            LlmEditIntentTranslator(FakeCaller(translation_reply)),
            supported_operations=set(HWP_OPERATION_GUIDE),
            composer=composer,
        )

    def test_a_proposal_is_shown_and_the_request_still_refused(self):
        analyzer = self._analyzer(plan(*GOOD))
        with self.assertRaises(Stage5EditError) as caught:
            analyzer.analyze("표 만들고 첫 칸에 제목 넣어줘", HWP)
        message = str(caught.exception)
        self.assertIn("아래 순서로는", message)
        self.assertIn("1.", message)

    def test_without_a_proposal_the_usual_guidance_is_kept(self):
        analyzer = self._analyzer(plan())
        with self.assertRaises(Stage5EditError) as caught:
            analyzer.analyze("맞춤법 검사해줘", HWP)
        self.assertIn("지원하는 한글 편집 예", str(caught.exception))

    def test_a_wording_the_rules_know_never_reaches_the_composer(self):
        composer = LlmMacroComposer(FakeCaller(plan(*GOOD)))
        analyzer = LlmAssistedEditIntentAnalyzer(
            StructuredEditIntentAnalyzer(),
            None,
            supported_operations=set(HWP_OPERATION_GUIDE),
            composer=composer,
        )
        self.assertEqual(
            "set_text_format", analyzer.analyze("굵게 해줘", HWP).operation
        )
        self.assertEqual([], composer._caller.calls)



class TableCreationIsNotACellAddressTests(unittest.TestCase):
    """Asking for a table is not asking for one of its cells."""

    def setUp(self):
        self.analyzer = StructuredEditIntentAnalyzer()

    def test_creating_a_table_does_not_read_its_size_as_a_cell(self):
        # `3행 2열 표 만들고 첫 칸에 “제목” 넣어줘` read 3행 2열 as a cell
        # address and tried to type into a table that did not exist yet.
        intent = self.analyzer.analyze(
            "3행 2열 표 만들고 첫 칸에 “제목” 넣어줘", HWP
        )
        self.assertEqual("insert_table", intent.operation)
        self.assertEqual({"rows": 3, "columns": 2}, intent.params)

    def test_writing_into_an_existing_table_still_reads_the_address(self):
        intent = self.analyzer.analyze("표 2행 3열에 “값” 넣어줘", HWP)
        self.assertEqual("set_table_cell", intent.operation)
        self.assertEqual(2, intent.params["row"])
        self.assertEqual(3, intent.params["column"])

if __name__ == "__main__":
    unittest.main()
