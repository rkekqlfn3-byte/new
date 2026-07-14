import os
import tempfile
import unittest
from unittest import mock

from engine.api import command_api
from engine.app_actions.base import AppActionBlocked, AppActionContextChanged
from engine.app_actions.value_normalizer import normalize_range_address
from engine.decision import DecisionEngine, PreferenceManager
from engine.execution_runtime import ExecutionController
from engine.app_actions.registry import AppActionRegistry
from engine.parser import CommandParser
from engine.test_excel_core_actions import FakeExcel, adapter_for


def seed_table(excel):
    sheet = excel.ActiveSheet
    values = (
        ("이름", "매출", "상태"),
        ("홍길동", 30, "진행"),
        ("김영희", 10, "완료"),
        ("홍길동", 20, "완료"),
    )
    for row, row_values in enumerate(values, start=1):
        for column, value in enumerate(row_values, start=1):
            sheet.Cells(row, column).Value2 = value
    return sheet


def parser_for(temp_dir, excel):
    parser = CommandParser()
    parser.execution_controller = ExecutionController(
        os.path.join(temp_dir, "diagnostics.json")
    )
    parser.preference_manager = PreferenceManager(
        os.path.join(temp_dir, "user_preferences.json")
    )
    parser.app_action_registry = AppActionRegistry({"excel": adapter_for(excel)})
    parser.action_executor.controller = parser.execution_controller
    parser.action_executor.app_action_registry = parser.app_action_registry
    parser.macro_runner.controller = parser.execution_controller
    parser.llm_engine.process_command = mock.Mock(
        side_effect=AssertionError("AI should not run")
    )
    return parser


class ExcelAuxAdapterTests(unittest.TestCase):
    def test_rectangular_range_normalization(self):
        self.assertEqual("A2:C10", normalize_range_address("$a$2:$c$10"))
        self.assertEqual("B3", normalize_range_address("b3"))
        with self.assertRaises(AppActionBlocked):
            normalize_range_address("C10:A2")

    def test_range_format_is_applied_and_verified(self):
        excel = FakeExcel()
        sheet = seed_table(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "format_range",
            {
                "range": "A2:C3",
                "bold": True,
                "font_size": 14,
                "alignment": "center",
                "fill_color": "yellow",
            },
        )
        result = adapter.execute(prepared)

        self.assertEqual(6, prepared.estimated_changes)
        self.assertTrue(result["verified"])
        for row in range(2, 4):
            for column in range(1, 4):
                cell = sheet.Cells(row, column)
                self.assertTrue(cell.Font.Bold)
                self.assertEqual(14, cell.Font.Size)
                self.assertEqual(-4108, cell.HorizontalAlignment)
                self.assertEqual(65535, cell.Interior.Color)

    def test_range_format_detects_stale_state_and_large_change_needs_confirmation(self):
        excel = FakeExcel()
        sheet = excel.ActiveSheet
        for row in range(1, 102):
            sheet.Cells(row, 1).Value2 = row
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "format_range", {"range": "A1:A101", "bold": True}
        )
        self.assertTrue(prepared.destructive)
        self.assertTrue(DecisionEngine().evaluate(prepared).requires_confirmation)
        sheet.Range("A1").Font.Bold = True
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)

    def test_filter_apply_and_clear_are_verified(self):
        excel = FakeExcel()
        seed_table(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "filter_range",
            {"column_name": "매출", "operator": ">=", "value": 20},
        )
        applied = adapter.execute(prepared)
        self.assertTrue(applied["verified"])
        self.assertTrue(excel.ActiveSheet.FilterMode)
        self.assertEqual(">=20", excel.ActiveSheet.AutoFilter.Filters.Item(2).Criteria1)

        clearing = adapter.prepare("filter_range", {"clear": True})
        cleared = adapter.execute(clearing)
        self.assertTrue(cleared["verified"])
        self.assertFalse(excel.ActiveSheet.FilterMode)

    def test_find_replace_skips_formulas_and_requires_confirmation(self):
        excel = FakeExcel()
        sheet = seed_table(excel)
        sheet.Range("D1").Formula = '="홍길동"'
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "find_replace",
            {
                "scope": "current_sheet",
                "find": "홍길동",
                "replace": "홍길순",
            },
        )
        self.assertEqual(2, prepared.current_state["matching_count"])
        self.assertTrue(DecisionEngine().evaluate(prepared).requires_confirmation)
        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual("홍길순", sheet.Range("A2").Value2)
        self.assertEqual("홍길순", sheet.Range("A4").Value2)
        self.assertTrue(sheet.Range("D1").HasFormula)

    def test_sort_moves_whole_rows_and_mixed_keys_are_blocked(self):
        excel = FakeExcel()
        sheet = seed_table(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "sort_range", {"column_name": "매출", "direction": "ascending"}
        )
        self.assertTrue(prepared.destructive)
        result = adapter.execute(prepared)
        self.assertTrue(result["verified"])
        self.assertEqual(("이름", "매출", "상태"), tuple(
            sheet.Cells(1, column).Value2 for column in range(1, 4)
        ))
        self.assertEqual(("김영희", 10, "완료"), tuple(
            sheet.Cells(2, column).Value2 for column in range(1, 4)
        ))
        self.assertEqual(("홍길동", 20, "완료"), tuple(
            sheet.Cells(3, column).Value2 for column in range(1, 4)
        ))

        sheet.Range("B3").Value2 = "문자"
        with self.assertRaises(AppActionBlocked):
            adapter.prepare(
                "sort_range", {"column_name": "매출", "direction": "descending"}
            )


class ExcelAuxParserTests(unittest.TestCase):
    def test_range_format_runs_locally_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-aux-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_table(excel)
            parser = parser_for(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 A2:C3를 굵게 하고 가운데 정렬해줘",
                    session_id="aux-format",
                )

        self.assertTrue(result["success"])
        self.assertTrue(sheet.Range("A2").Font.Bold)
        self.assertEqual(-4108, sheet.Range("C3").HorizontalAlignment)
        parser.llm_engine.process_command.assert_not_called()

    def test_filter_command_and_clear_run_locally(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-aux-") as temp_dir:
            excel = FakeExcel()
            seed_table(excel)
            parser = parser_for(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                applied = command_api.parse_command(
                    "엑셀 매출 열에서 20 이상만 필터해줘",
                    session_id="aux-filter",
                )
                cleared = command_api.parse_command(
                    "엑셀 필터를 해제해줘",
                    session_id="aux-filter-clear",
                )

        self.assertTrue(applied["success"])
        self.assertTrue(cleared["success"])
        self.assertFalse(excel.ActiveSheet.FilterMode)
        parser.llm_engine.process_command.assert_not_called()

    def test_find_replace_changes_only_after_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-aux-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_table(excel)
            parser = parser_for(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 A2:A4에서 홍길동을 홍길순으로 바꿔줘",
                    session_id="aux-replace",
                )
                before = sheet.Range("A2").Value2
                completed = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "apply",
                    "aux-replace",
                )

        self.assertEqual("confirmation_required", first["status"])
        self.assertEqual("홍길동", before)
        self.assertTrue(completed["success"])
        self.assertEqual("홍길순", sheet.Range("A2").Value2)
        self.assertEqual("홍길순", sheet.Range("A4").Value2)

    def test_sort_requires_confirmation_and_preserves_rows(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-aux-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_table(excel)
            parser = parser_for(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 매출 열을 오름차순으로 정렬해줘",
                    session_id="aux-sort",
                )
                before = tuple(sheet.Cells(2, column).Value2 for column in range(1, 4))
                completed = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "apply",
                    "aux-sort",
                )

        self.assertEqual("confirmation_required", first["status"])
        self.assertEqual(("홍길동", 30, "진행"), before)
        self.assertTrue(completed["success"])
        self.assertEqual(("김영희", 10, "완료"), tuple(
            sheet.Cells(2, column).Value2 for column in range(1, 4)
        ))


if __name__ == "__main__":
    unittest.main()
