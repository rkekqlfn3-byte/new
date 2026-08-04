"""Contract tests for the per-operation adapter structure.

A new native action must be a new module under
``engine/app_actions/operations/`` rather than another branch inside an
adapter that is already at its maintenance budget.  These tests pin the
registry contract and the delegation, so the structure cannot quietly rot back
into per-adapter dispatch chains.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from engine.app_actions.base import AppActionBlocked, PreparedAction
from engine.app_actions.hwp_adapter import HwpAdapter
from engine.app_actions.operations import AppOperation, OperationRegistry
from engine.app_actions.operations.hwp import HWP_OPERATIONS
from engine.app_actions.operations.powerpoint import POWERPOINT_OPERATIONS
from engine.app_actions.operations.powerpoint.base import (
    PowerPointOperation,
    PowerPointSession,
)
from engine.app_actions.operations.word import WORD_OPERATIONS
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.word_adapter import WordAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MIGRATED_ADAPTERS = {
    "hwp": (HwpAdapter, HWP_OPERATIONS, "hwp_adapter.py"),
    "word": (WordAdapter, WORD_OPERATIONS, "word_adapter.py"),
    "powerpoint": (
        PowerPointAdapter,
        POWERPOINT_OPERATIONS,
        "powerpoint_adapter.py",
    ),
}


def prepared(app="hwp", operation="insert_text", **overrides):
    return PreparedAction(
        app=app,
        operation=operation,
        document_id="doc",
        workbook_name="doc.hwp",
        sheet="현재 문서",
        target="선택 영역",
        params={},
        current_state={"slide_id": 256, "slide_number": 1, "shape_id": 3},
        estimated_changes=1,
        destructive=False,
        reversible=True,
        verification_method="test",
        context_fingerprint="fingerprint",
        prepared_at="2026-08-04T00:00:00+09:00",
        **overrides,
    )


class OperationRegistryTests(unittest.TestCase):
    def test_registry_rejects_unnamed_mismatched_and_duplicate_operations(self):
        class Unnamed:
            app = "hwp"
            name = ""

        class WrongApp:
            app = "word"
            name = "insert_text"

        class Named:
            app = "hwp"
            name = "insert_text"

        with self.assertRaises(ValueError):
            OperationRegistry("hwp", "한글", (Unnamed(),))
        with self.assertRaises(ValueError):
            OperationRegistry("hwp", "한글", (WrongApp(),))
        with self.assertRaises(ValueError):
            OperationRegistry("hwp", "한글", (Named(), Named()))

    def test_unknown_operation_is_blocked_with_the_app_label(self):
        with self.assertRaises(AppActionBlocked) as caught:
            HWP_OPERATIONS.require("stamp_the_document")
        self.assertIn("한글", str(caught.exception))
        self.assertIn("stamp_the_document", str(caught.exception))

    def test_prepared_actions_from_another_app_are_blocked(self):
        with self.assertRaises(AppActionBlocked):
            HWP_OPERATIONS.require_prepared(prepared(app="word"))
        with self.assertRaises(AppActionBlocked):
            HWP_OPERATIONS.require_prepared(prepared(operation="launch_missiles"))
        self.assertIsNotNone(HWP_OPERATIONS.require_prepared(prepared()))


class MigratedAdapterStructureTests(unittest.TestCase):
    def test_adapters_report_exactly_the_registered_operations(self):
        for app, (adapter, registry, _) in MIGRATED_ADAPTERS.items():
            with self.subTest(app=app):
                self.assertEqual(registry.names, adapter.supported_operations)

    def test_every_registered_operation_satisfies_the_contract(self):
        for app, (_, registry, _) in MIGRATED_ADAPTERS.items():
            for name in sorted(registry.names):
                with self.subTest(app=app, operation=name):
                    operation = registry.require(name)
                    self.assertIsInstance(operation, AppOperation)
                    self.assertEqual(app, operation.app)
                    self.assertEqual(name, operation.name)

    def test_adapters_no_longer_carry_per_operation_methods(self):
        # Per-operation prepare/execute methods on the adapter are what the
        # split removed. ``undo`` still branches by operation name on purpose:
        # restoring is the adapter's job, not the operation's.
        for app, (adapter, _, _) in MIGRATED_ADAPTERS.items():
            leftovers = sorted(
                name
                for name in vars(adapter)
                if name.startswith(("_prepare_", "_execute_"))
            )
            with self.subTest(app=app):
                self.assertEqual([], leftovers)

    def test_operation_modules_cover_every_registered_operation(self):
        # The point of the split: adding an action must not edit the adapter.
        for app, (_, registry, _) in MIGRATED_ADAPTERS.items():
            operations_dir = (
                PROJECT_ROOT / "engine" / "app_actions" / "operations" / app
            )
            modules = {
                path.stem
                for path in operations_dir.glob("*.py")
                if path.stem not in {"__init__", "base", "state"}
            }
            with self.subTest(app=app):
                self.assertEqual(len(registry.names), len(modules))


class HwpOperationStructureTests(unittest.TestCase):
    def test_registered_hwp_operations_are_stable(self):
        self.assertEqual(
            {
                "insert_text",
                "set_text_format",
                "set_paragraph_format",
                "find_replace",
                "save_as",
            },
            set(HWP_OPERATIONS.names),
        )

    def test_registered_powerpoint_operations_are_stable(self):
        self.assertEqual(
            {
                "replace_shape_text",
                "set_text_format",
                "set_text_alignment",
                "move_shape",
                "resize_shape",
                "match_previous_style",
            },
            set(POWERPOINT_OPERATIONS.names),
        )

    def test_registered_word_operations_are_stable(self):
        self.assertEqual(
            {
                "replace_selection",
                "set_text_format",
                "set_paragraph_format",
                "save_document",
            },
            set(WORD_OPERATIONS.names),
        )


class PowerPointWriteRetrySafetyTests(unittest.TestCase):
    """The write flag decides whether a transient COM failure may be retried.

    A retry after a write has begun would apply the change twice, so the flag
    must be raised before the first write and must still be readable from the
    attempt that raised.
    """

    def setUp(self):
        self.session = PowerPointSession(application=object(), presentation=object())

    def test_session_starts_with_no_write_recorded(self):
        self.assertFalse(self.session.write_started)

    def test_operation_raises_the_flag_only_after_resolving_the_target(self):
        events = []

        class RecordingAdapter:
            def _ensure_same_context(self, current, prepared):
                events.append("fingerprint")

            def _slide_by_id(self, presentation, slide_id, slide_number):
                events.append("slide")
                return "slide"

            def _shape_by_id(self, slide, shape_id):
                events.append("shape")
                return "shape"

            def _result(self, current, changed, observations):
                return {"noop": True}

        class Operation(PowerPointOperation):
            name = "replace_shape_text"

            def prepare(inner, adapter, session, params):
                events.append("prepare")
                self.assertFalse(session.write_started)
                return prepared(app="powerpoint", operation="replace_shape_text")

            def run(inner, adapter, target, current):
                events.append("run")
                self.assertTrue(self.session.write_started)
                return {"ok": True}

        result = Operation().execute(
            RecordingAdapter(),
            self.session,
            prepared(app="powerpoint", operation="replace_shape_text"),
        )
        self.assertEqual({"ok": True}, result)
        self.assertEqual(
            ["prepare", "fingerprint", "slide", "shape", "run"], events
        )
        self.assertTrue(self.session.write_started)

    def test_a_noop_never_records_a_write(self):
        class NoopAdapter:
            def _ensure_same_context(self, current, prepared):
                pass

            def _result(self, current, changed, observations):
                return {"noop": True}

        noop_action = prepared(
            app="powerpoint", operation="replace_shape_text", noop=True
        )

        class Operation(PowerPointOperation):
            name = "replace_shape_text"

            def prepare(inner, adapter, session, params):
                return noop_action

        Operation().execute(NoopAdapter(), self.session, noop_action)
        self.assertFalse(self.session.write_started)

    def test_a_failure_before_the_write_leaves_the_flag_down(self):
        class FailingAdapter:
            def _ensure_same_context(self, current, prepared):
                raise RuntimeError("context moved")

        class Operation(PowerPointOperation):
            name = "replace_shape_text"

            def prepare(inner, adapter, session, params):
                return prepared(app="powerpoint", operation="replace_shape_text")

        with self.assertRaises(RuntimeError):
            Operation().execute(
                FailingAdapter(),
                self.session,
                prepared(app="powerpoint", operation="replace_shape_text"),
            )
        # Nothing was written, so a proxy re-acquire retry stays allowed.
        self.assertFalse(self.session.write_started)

    def test_a_failure_during_the_write_keeps_the_flag_raised(self):
        class WritingAdapter:
            def _ensure_same_context(self, current, prepared):
                pass

            def _slide_by_id(self, presentation, slide_id, slide_number):
                return "slide"

            def _shape_by_id(self, slide, shape_id):
                return "shape"

        class Operation(PowerPointOperation):
            name = "replace_shape_text"

            def prepare(inner, adapter, session, params):
                return prepared(app="powerpoint", operation="replace_shape_text")

            def run(inner, adapter, target, current):
                raise RuntimeError("COM disconnected mid-write")

        with self.assertRaises(RuntimeError):
            Operation().execute(
                WritingAdapter(),
                self.session,
                prepared(app="powerpoint", operation="replace_shape_text"),
            )
        # The write began, so the change must never be replayed.
        self.assertTrue(self.session.write_started)


if __name__ == "__main__":
    unittest.main()
