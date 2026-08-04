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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def prepared(app="hwp", operation="insert_text"):
    return PreparedAction(
        app=app,
        operation=operation,
        document_id="doc",
        workbook_name="doc.hwp",
        sheet="현재 문서",
        target="선택 영역",
        params={},
        current_state={},
        estimated_changes=1,
        destructive=False,
        reversible=True,
        verification_method="test",
        context_fingerprint="fingerprint",
        prepared_at="2026-08-04T00:00:00+09:00",
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


class HwpOperationStructureTests(unittest.TestCase):
    def test_adapter_reports_exactly_the_registered_operations(self):
        self.assertEqual(HWP_OPERATIONS.names, HwpAdapter.supported_operations)
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

    def test_every_registered_operation_satisfies_the_contract(self):
        for name in sorted(HWP_OPERATIONS.names):
            with self.subTest(operation=name):
                operation = HWP_OPERATIONS.require(name)
                self.assertIsInstance(operation, AppOperation)
                self.assertEqual("hwp", operation.app)
                self.assertEqual(name, operation.name)

    def test_adapter_no_longer_carries_per_operation_methods(self):
        source = (
            PROJECT_ROOT / "engine" / "app_actions" / "hwp_adapter.py"
        ).read_text(encoding="utf-8")
        for moved in (
            "_prepare_insert",
            "_execute_insert",
            "_prepare_text_format",
            "_prepare_paragraph_format",
            "_prepare_find_replace",
            "_prepare_save_as",
        ):
            with self.subTest(method=moved):
                self.assertNotIn(f"def {moved}", source)

    def test_operations_are_reachable_without_touching_the_adapter_file(self):
        # The point of the split: adding an action must not edit the adapter.
        operations_dir = (
            PROJECT_ROOT / "engine" / "app_actions" / "operations" / "hwp"
        )
        modules = {
            path.stem
            for path in operations_dir.glob("*.py")
            if path.stem not in {"__init__", "base", "state"}
        }
        self.assertEqual(
            {
                "insert_text",
                "text_format",
                "paragraph_format",
                "find_replace",
                "save_as",
            },
            modules,
        )


if __name__ == "__main__":
    unittest.main()
