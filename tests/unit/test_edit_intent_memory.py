"""Remembering what the provider worked out.

Layer 2 asks a provider for every unrecognised sentence, every time, so
saying 행간 1.5로 a hundred times costs a hundred requests. These pin the
fourth layer of the contract: what gets written down, what must not, and
that the second request for a remembered wording never leaves the machine.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.edit_mode.intent_memory import EditIntentMemory, normalise
from engine.edit_mode.llm_intent import (
    HWP_OPERATION_GUIDE,
    LlmAssistedEditIntentAnalyzer,
    LlmEditIntentTranslator,
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
        return self.reply


def reply(operation, params=None):
    body = json.dumps(
        {"operation": operation, "params": params or {}, "description": "설명"},
        ensure_ascii=False,
    )
    return json.dumps({"response": body}, ensure_ascii=False)


class MemoryStoreTests(unittest.TestCase):
    def _memory(self, temp_dir):
        return EditIntentMemory(Path(temp_dir) / "memory.json")

    def test_a_remembered_wording_comes_back(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            memory.remember("hwp", "행간 1.5로", "set_line_spacing", {"line_spacing": 150})

            self.assertEqual(
                ("set_line_spacing", {"line_spacing": 150}),
                memory.recall("hwp", "행간 1.5로"),
            )

    def test_spacing_and_case_do_not_make_a_different_wording(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            memory.remember("hwp", "행간 1.5로", "set_line_spacing", {})

            self.assertIsNotNone(memory.recall("hwp", "  행간   1.5로 "))

    def test_a_different_wording_is_a_different_note(self):
        # Matching loosely would apply the first sentence's numbers to the
        # second, which turns a saved request into a wrong edit.
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            memory.remember("hwp", "행간 1.5로", "set_line_spacing", {"line_spacing": 150})

            self.assertIsNone(memory.recall("hwp", "행간 2로"))

    def test_applications_are_remembered_separately(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            memory.remember("hwp", "굵게", "set_text_format", {})

            self.assertIsNone(memory.recall("excel", "굵게"))

    def test_a_note_survives_a_restart(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            path = Path(temp_dir) / "memory.json"
            EditIntentMemory(path).remember("hwp", "행간 1.5로", "set_line_spacing", {})

            self.assertIsNotNone(EditIntentMemory(path).recall("hwp", "행간 1.5로"))

    def test_forgetting_one_application_leaves_the_others(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            memory.remember("hwp", "행간 1.5로", "set_line_spacing", {})
            memory.remember("excel", "노랗게", "format_range", {})

            self.assertEqual(1, memory.forget("hwp"))
            self.assertIsNone(memory.recall("hwp", "행간 1.5로"))
            self.assertIsNotNone(memory.recall("excel", "노랗게"))

    def test_an_empty_command_is_never_remembered(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = self._memory(temp_dir)
            self.assertFalse(memory.remember("hwp", "   ", "set_text_format", {}))

    def test_normalise_is_what_two_requests_must_share(self):
        self.assertEqual("행간 1.5로", normalise("  행간\t1.5로 "))


class RecallBeforeProviderTests(unittest.TestCase):
    def _analyzer(self, memory, caller=None):
        caller = caller or FakeCaller(reply("set_list_format", {"list_format": "number"}))
        analyzer = LlmAssistedEditIntentAnalyzer(
            StructuredEditIntentAnalyzer(),
            LlmEditIntentTranslator(caller),
            supported_operations=set(HWP_OPERATION_GUIDE),
            memory=memory,
        )
        return analyzer, caller

    def test_a_remembered_wording_never_reaches_the_provider(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            memory.remember(
                "hwp", "번호 매겨줘", "set_list_format", {"list_format": "number"}
            )
            analyzer, caller = self._analyzer(memory)

            intent = analyzer.analyze("번호 매겨줘", HWP)

            self.assertEqual("set_list_format", intent.operation)
            self.assertEqual({"list_format": "number"}, intent.params)
            self.assertEqual("memory", intent.source)
            self.assertEqual([], caller.calls)

    def test_an_unremembered_wording_still_asks(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            analyzer, caller = self._analyzer(memory)

            intent = analyzer.analyze("번호 매겨줘", HWP)

            self.assertEqual("provider", intent.source)
            self.assertEqual(1, len(caller.calls))

    def test_the_rules_still_answer_first(self):
        # A note must never shadow an operation the rules already handle.
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            memory.remember("hwp", "굵게 해줘", "delete_text", {})
            analyzer, caller = self._analyzer(memory)

            intent = analyzer.analyze("굵게 해줘", HWP)

            self.assertEqual("set_text_format", intent.operation)
            self.assertEqual("rules", intent.source)

    def test_a_note_for_an_operation_no_longer_allowed_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            memory.remember("hwp", "번호 매겨줘", "set_list_format", {})
            analyzer = LlmAssistedEditIntentAnalyzer(
                StructuredEditIntentAnalyzer(),
                None,
                supported_operations={"insert_text"},
                memory=memory,
            )

            with self.assertRaises(Stage5EditError):
                analyzer.analyze("번호 매겨줘", HWP)


class OnlySuccessIsRememberedTests(unittest.TestCase):
    """A cancelled or unverified translation is not evidence of meaning."""

    def _adapter(self, memory):
        from engine.edit_mode.stage5 import Stage5NativeEditAdapter

        adapter = Stage5NativeEditAdapter.__new__(Stage5NativeEditAdapter)
        adapter.intent_memory = memory
        return adapter

    def _prepared(self, source="provider"):
        class Prepared:
            app_type = "hwp"
            operation = "set_list_format"
            metadata = {
                "intent_source": source,
                "intent_command": "번호 매겨줘",
                "intent_params": {"list_format": "number"},
            }

        return Prepared()

    def test_a_verified_provider_translation_is_written_down(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            self._adapter(memory)._remember_intent(
                self._prepared(), {"verified": True}
            )

            self.assertIsNotNone(memory.recall("hwp", "번호 매겨줘"))

    def test_an_unverified_result_is_not_written_down(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            self._adapter(memory)._remember_intent(
                self._prepared(), {"verified": False}
            )

            self.assertIsNone(memory.recall("hwp", "번호 매겨줘"))

    def test_a_rule_answer_is_not_written_down(self):
        # The rules already know it; a note would only be another place to
        # keep the same thing in step.
        with tempfile.TemporaryDirectory(prefix="jarvis-memory-") as temp_dir:
            memory = EditIntentMemory(Path(temp_dir) / "memory.json")
            self._adapter(memory)._remember_intent(
                self._prepared(source="rules"), {"verified": True}
            )

            self.assertIsNone(memory.recall("hwp", "번호 매겨줘"))

    def test_a_failing_store_never_fails_the_edit(self):
        class BrokenMemory:
            def remember(self, *_args, **_kwargs):
                raise OSError("disk full")

        self._adapter(BrokenMemory())._remember_intent(
            self._prepared(), {"verified": True}
        )


if __name__ == "__main__":
    unittest.main()
