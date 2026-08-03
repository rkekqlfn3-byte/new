import os
import re
import tempfile
import unittest
from unittest import mock

from engine.api import command_api
from engine.app_actions.base import AppActionBlocked, AppActionContextChanged
from engine.app_actions.excel_adapter import XL_NONE, ExcelAdapter
from engine.app_actions.registry import AppActionRegistry
from engine.app_actions.value_normalizer import excel_column_letters, excel_column_number
from engine.decision import PreferenceManager
from engine.execution_runtime import ExecutionController
from engine.parser import CommandParser


class FakeInterior:
    def __init__(self):
        self._color = 16777215
        self._color_index = XL_NONE

    @property
    def Color(self):
        return self._color

    @Color.setter
    def Color(self, value):
        self._color = int(value)
        self._color_index = 1

    @property
    def ColorIndex(self):
        return self._color_index

    @ColorIndex.setter
    def ColorIndex(self, value):
        self._color_index = int(value)
        if self._color_index == XL_NONE:
            self._color = 16777215


class FakeFont:
    def __init__(self):
        self.Bold = False
        self.Size = 11
        self.Color = 0


class FakeCell:
    Count = 1
    CountLarge = 1
    MergeCells = False

    def __init__(self, sheet, row, column):
        self.sheet = sheet
        self.Row = row
        self.Column = column
        self._value = None
        self._formula = None
        self.Interior = FakeInterior()
        self.Font = FakeFont()
        self.HorizontalAlignment = -4131

    @property
    def address(self):
        return f"{excel_column_letters(self.Column)}{self.Row}"

    def Address(self, *_args):
        return self.address

    @property
    def HasFormula(self):
        return self._formula is not None

    @property
    def Formula(self):
        return self._formula if self._formula is not None else self._value

    @Formula.setter
    def Formula(self, value):
        self._formula = str(value)
        self._value = 0

    @property
    def Value2(self):
        return self._value

    @Value2.setter
    def Value2(self, value):
        self._formula = None
        self._value = value

    def End(self, _direction):
        rows = [
            row for (row, column), cell in self.sheet._cells.items()
            if column == self.Column and (cell.Value2 not in {None, ""} or cell.HasFormula)
        ]
        return self.sheet._cell(max(rows) if rows else 1, self.Column)


class FakeCondition:
    def __init__(self, collection, condition_type, operator, formula1):
        self.collection = collection
        self.Type = condition_type
        self.Operator = operator
        self.Formula1 = formula1
        self.Formula2 = ""
        self.Interior = FakeInterior()

    def Delete(self):
        self.collection.items.remove(self)


class FakeConditions:
    def __init__(self):
        self.items = []

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]

    def Add(self, **kwargs):
        condition = FakeCondition(
            self,
            kwargs.get("Type"),
            kwargs.get("Operator"),
            kwargs.get("Formula1"),
        )
        self.items.append(condition)
        return condition


class FakeRangeFont:
    def __init__(self, source):
        self.source = source

    @property
    def Bold(self):
        return self.source._all_cells[0].Font.Bold

    @Bold.setter
    def Bold(self, value):
        for cell in self.source._all_cells:
            cell.Font.Bold = bool(value)

    @property
    def Size(self):
        return self.source._all_cells[0].Font.Size

    @Size.setter
    def Size(self, value):
        for cell in self.source._all_cells:
            cell.Font.Size = value

    @property
    def Color(self):
        return self.source._all_cells[0].Font.Color

    @Color.setter
    def Color(self, value):
        for cell in self.source._all_cells:
            cell.Font.Color = int(value)


class FakeRangeInterior:
    def __init__(self, source):
        self.source = source

    @property
    def Color(self):
        return self.source._all_cells[0].Interior.Color

    @Color.setter
    def Color(self, value):
        for cell in self.source._all_cells:
            cell.Interior.Color = value


class FakeFilter:
    def __init__(self):
        self.On = False
        self.Criteria1 = None
        self.Operator = 0
        self.Criteria2 = None


class FakeFilters:
    def __init__(self, count):
        self.items = [FakeFilter() for _ in range(count)]

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index - 1]


class FakeAutoFilter:
    def __init__(self, source):
        self.Range = source
        self.Filters = FakeFilters(source.last_column - source.first_column + 1)


class FakeRange:
    def __init__(self, sheet, first_row, first_column, last_row, last_column):
        self.sheet = sheet
        self.first_row = first_row
        self.first_column = first_column
        self.last_row = last_row
        self.last_column = last_column

    @property
    def _all_cells(self):
        return [
            self.sheet._cell(row, column)
            for row in range(self.first_row, self.last_row + 1)
            for column in range(self.first_column, self.last_column + 1)
        ]

    @property
    def Count(self):
        return len(self._all_cells)

    @property
    def CountLarge(self):
        return self.Count

    @property
    def Cells(self):
        return self

    def Item(self, index):
        return self._all_cells[index - 1]

    def __call__(self, index):
        return self.Item(index)

    @property
    def MergeCells(self):
        return any(cell.MergeCells for cell in self._all_cells)

    def Address(self, *_args):
        first = f"{excel_column_letters(self.first_column)}{self.first_row}"
        if self.Count == 1:
            return first
        last = f"{excel_column_letters(self.last_column)}{self.last_row}"
        return f"{first}:{last}"

    def _matrix(self, attribute):
        rows = []
        for row in range(self.first_row, self.last_row + 1):
            rows.append(tuple(
                getattr(self.sheet._cell(row, column), attribute)
                for column in range(self.first_column, self.last_column + 1)
            ))
        if self.Count == 1:
            return rows[0][0]
        return tuple(rows)

    @property
    def Value2(self):
        return self._matrix("Value2")

    @property
    def Formula(self):
        return self._matrix("Formula")

    @property
    def Font(self):
        return FakeRangeFont(self)

    @property
    def Interior(self):
        return FakeRangeInterior(self)

    @property
    def HorizontalAlignment(self):
        return self._all_cells[0].HorizontalAlignment

    @HorizontalAlignment.setter
    def HorizontalAlignment(self, value):
        for cell in self._all_cells:
            cell.HorizontalAlignment = int(value)

    @property
    def FormatConditions(self):
        key = self.Address(False, False)
        return self.sheet._conditions.setdefault(key, FakeConditions())

    def AutoFilter(self, **kwargs):
        if (
            self.sheet.AutoFilter is None
            or self.sheet.AutoFilter.Range.Address(False, False) != self.Address(False, False)
        ):
            self.sheet.AutoFilter = FakeAutoFilter(self)
        self.sheet.AutoFilterMode = True
        field = int(kwargs.get("Field", 1))
        item = self.sheet.AutoFilter.Filters.Item(field)
        item.On = True
        item.Criteria1 = kwargs.get("Criteria1")
        item.Operator = kwargs.get("Operator", 0)
        item.Criteria2 = kwargs.get("Criteria2")
        self.sheet.FilterMode = True

    def Sort(self, **kwargs):
        if int(kwargs.get("Orientation", 1)) != 1:
            raise ValueError("table rows must be sorted top-to-bottom")
        key_source = kwargs["Key1"]
        has_header = int(kwargs.get("Header", 2)) == 1
        first_data_row = self.first_row + 1 if has_header else self.first_row
        if key_source.first_row != first_data_row:
            raise ValueError("sort key must exclude the header row")
        key_column = getattr(key_source, "first_column", None)
        if key_column is None:
            key_column = key_source.Column
        descending = int(kwargs.get("Order1", 1)) == 2
        rows = []
        for row in range(first_data_row, self.last_row + 1):
            cells = [
                self.sheet._cell(row, column)
                for column in range(self.first_column, self.last_column + 1)
            ]
            key_value = self.sheet._cell(row, key_column).Value2
            rows.append((key_value, [
                (cell._formula, cell._value) for cell in cells
            ]))
        nonblank = [item for item in rows if item[0] not in {None, ""}]
        blank = [item for item in rows if item[0] in {None, ""}]
        nonblank.sort(
            key=lambda item: (
                float(item[0])
                if isinstance(item[0], (int, float)) else str(item[0]).casefold()
            ),
            reverse=descending,
        )
        for row_number, (_, values) in enumerate(
            nonblank + blank, start=first_data_row
        ):
            for offset, (formula, value) in enumerate(values):
                cell = self.sheet._cell(row_number, self.first_column + offset)
                cell._formula = formula
                cell._value = value


class FakeCellsAccessor:
    def __init__(self, sheet):
        self.sheet = sheet

    def __call__(self, row, column):
        return self.sheet._cell(int(row), int(column))


class FakeRows:
    Count = 1048576


class FakeColumns:
    def __init__(self, count):
        self.Count = count


class FakeUsedRange:
    def __init__(self, sheet):
        used = [
            (row, column) for (row, column), cell in sheet._cells.items()
            if cell.Value2 not in {None, ""} or cell.HasFormula
        ]
        rows = [row for row, _ in used] or [1]
        columns = [column for _, column in used] or [1]
        self.first_row = min(rows)
        self.last_row = max(rows)
        self.first_column = min(columns)
        self.last_column = max(columns)
        self.Row = self.first_row
        self.Column = min(columns)
        self.Columns = FakeColumns(max(columns) - min(columns) + 1)

    def Address(self, *_args):
        first = f"{excel_column_letters(self.first_column)}{self.first_row}"
        if self.first_row == self.last_row and self.first_column == self.last_column:
            return first
        last = f"{excel_column_letters(self.last_column)}{self.last_row}"
        return f"{first}:{last}"


class FakeSheet:
    Type = -4167
    ProtectContents = False

    def __init__(self, name="Sheet1"):
        self.Name = name
        self._cells = {}
        self._conditions = {}
        self.Cells = FakeCellsAccessor(self)
        self.Rows = FakeRows()
        self.AutoFilterMode = False
        self.FilterMode = False
        self.AutoFilter = None

    def _cell(self, row, column):
        return self._cells.setdefault((row, column), FakeCell(self, row, column))

    def Range(self, address):
        text = str(address).replace("$", "").upper()
        match = re.fullmatch(r"([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?", text)
        if not match:
            raise ValueError(address)
        first_column, first_row, last_column, last_row = match.groups()
        return FakeRange(
            self,
            int(first_row),
            excel_column_number(first_column),
            int(last_row or first_row),
            excel_column_number(last_column or first_column),
        ) if last_column else self._cell(int(first_row), excel_column_number(first_column))

    @property
    def UsedRange(self):
        return FakeUsedRange(self)

    def ShowAllData(self):
        if self.AutoFilter is not None:
            for item in self.AutoFilter.Filters.items:
                item.On = False
                item.Criteria1 = None
                item.Operator = 0
                item.Criteria2 = None
        self.FilterMode = False


class FakeWorkbook:
    ReadOnly = False

    def __init__(self, sheet):
        self.Name = "Stage4.xlsx"
        self.Path = "C:\\JarvisTests"
        self.FullName = os.path.join(self.Path, self.Name)
        self.sheet = sheet


class FakeWorksheetFunction:
    def Sum(self, source):
        return sum(
            value for value in source.Value2
            for value in (value if isinstance(value, tuple) else (value,))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )

    def Average(self, source):
        values = [
            value for row in source.Value2
            for value in (row if isinstance(row, tuple) else (row,))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        return sum(values) / len(values)


class FakeExcel:
    Hwnd = 45678

    def __init__(self):
        self.ActiveSheet = FakeSheet()
        self.ActiveWorkbook = FakeWorkbook(self.ActiveSheet)
        self.WorksheetFunction = FakeWorksheetFunction()

    def Undo(self):
        return None


def adapter_for(excel):
    return ExcelAdapter(
        application_getter=lambda: excel,
        process_counter=lambda: 1,
        discovery_retry_delay=0,
    )


def seed_sales(excel):
    sheet = excel.ActiveSheet
    sheet.Range("A1").Value2 = "매출"
    sheet.Range("A2").Value2 = 40
    sheet.Range("A3").Value2 = 50
    sheet.Range("A4").Value2 = 70
    return sheet


class ExcelCoreAdapterTests(unittest.TestCase):
    def test_sum_formula_is_prepared_written_and_verified(self):
        excel = FakeExcel()
        sheet = seed_sales(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "sum_column_to_cell",
            {"column_name": "매출", "target_cell": "B1"},
        )
        result = adapter.execute(prepared)

        self.assertEqual("A2:A4", prepared.params["source_range"])
        self.assertEqual("=SUM(A2:A4)", sheet.Range("B1").Formula)
        self.assertTrue(result["verified"])

    def test_fixed_sum_and_circular_reference_guard(self):
        excel = FakeExcel()
        sheet = seed_sales(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "sum_column_to_cell",
            {"source_range": "A2:A4", "target_cell": "B1", "result_mode": "value"},
        )
        adapter.execute(prepared)
        self.assertEqual(160, sheet.Range("B1").Value2)

        with self.assertRaises(AppActionBlocked):
            adapter.prepare(
                "sum_column_to_cell",
                {"source_range": "A2:A4", "target_cell": "A3"},
            )

    def test_source_change_blocks_stale_sum(self):
        excel = FakeExcel()
        sheet = seed_sales(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "sum_column_to_cell",
            {"source_range": "A2:A4", "target_cell": "B1"},
        )
        sheet.Range("A2").Value2 = 99
        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)
        self.assertIsNone(sheet.Range("B1").Value2)

    def test_conditional_format_is_added_and_duplicate_is_noop(self):
        excel = FakeExcel()
        sheet = seed_sales(excel)
        adapter = adapter_for(excel)
        params = {
            "column_name": "매출", "operator": "ge", "threshold": 50,
            "color": "yellow",
        }
        first = adapter.prepare("apply_conditional_format", params)
        result = adapter.execute(first)
        second = adapter.prepare("apply_conditional_format", params)
        unchanged = adapter.execute(second)

        self.assertTrue(result["changed"])
        self.assertEqual(1, sheet.Range("A2:A4").FormatConditions.Count)
        self.assertTrue(second.noop)
        self.assertFalse(unchanged["changed"])

    def test_direct_format_only_changes_current_matches(self):
        excel = FakeExcel()
        sheet = seed_sales(excel)
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "format_matching_values",
            {"source_range": "A2:A4", "operator": ">=", "threshold": 50, "color": "노란색"},
        )
        result = adapter.execute(prepared)

        self.assertEqual(2, prepared.current_state["matching_count"])
        self.assertEqual(XL_NONE, sheet.Range("A2").Interior.ColorIndex)
        self.assertEqual(65535, sheet.Range("A3").Interior.Color)
        self.assertEqual(65535, sheet.Range("A4").Interior.Color)
        self.assertTrue(result["verified"])


class ExcelCoreParserTests(unittest.TestCase):
    @staticmethod
    def _parser(temp_dir, excel):
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
        return parser

    def test_local_sum_command_uses_header_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            parser.llm_engine.process_command = mock.Mock(
                side_effect=AssertionError("AI should not run")
            )
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 매출 열 합계를 B1에 넣어줘", session_id="sum-local"
                )

        self.assertTrue(result["success"])
        self.assertEqual("=SUM(A2:A4)", sheet.Range("B1").Formula)
        parser.llm_engine.process_command.assert_not_called()

    def test_incomplete_sum_waits_for_details_then_reuses_same_execution(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            parser.llm_engine.process_command = mock.Mock(
                side_effect=AssertionError("AI should not run")
            )
            with mock.patch.object(command_api, "parser", parser):
                waiting = command_api.parse_command(
                    "엑셀에서 합계 내줘", session_id="sum-clarification"
                )
                before = sheet.Range("C1").Value2
                completed = command_api.parse_command(
                    "엑셀에서 A2:A4 합계를 C1에 넣어줘",
                    session_id="sum-clarification",
                )

        self.assertEqual("clarification_required", waiting["status"])
        clarification = waiting["data"]["clarification"]
        self.assertEqual("missing_range", clarification["reason"])
        self.assertEqual("clarification", clarification["request_kind"])
        self.assertIsNone(before)
        self.assertTrue(completed["success"])
        self.assertEqual("=SUM(A2:A4)", sheet.Range("C1").Formula)
        records = parser.execution_controller.diagnostics(2)["records"]
        self.assertEqual(1, len(records))
        self.assertEqual("success", records[0]["status"])
        parser.llm_engine.process_command.assert_not_called()

    def test_incomplete_filter_and_sort_return_specific_reasons(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            parser = self._parser(temp_dir, FakeExcel())
            with mock.patch.object(command_api, "parser", parser):
                filter_waiting = command_api.parse_command(
                    "엑셀에서 필터 걸어줘", session_id="filter-clarification"
                )
                command_api.resolve_confirmation(
                    filter_waiting["data"]["confirmation"]["confirmation_id"],
                    "cancel",
                    "filter-clarification",
                )
                sort_waiting = command_api.parse_command(
                    "엑셀에서 정렬해줘", session_id="sort-clarification"
                )

        self.assertEqual(
            "missing_filter_condition",
            filter_waiting["data"]["clarification"]["reason"],
        )
        self.assertEqual(
            "missing_sort_key",
            sort_waiting["data"]["clarification"]["reason"],
        )

    def test_incomplete_sum_discards_answer_after_sheet_context_changes(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                waiting = command_api.parse_command(
                    "엑셀에서 합계 내줘", session_id="sum-stale-context"
                )
                sheet.Name = "다른시트"
                blocked = command_api.parse_command(
                    "엑셀에서 A2:A4 합계를 C1에 넣어줘",
                    session_id="sum-stale-context",
                )

        self.assertEqual("clarification_required", waiting["status"])
        self.assertFalse(blocked["success"])
        self.assertEqual("context_changed", blocked["status"])
        self.assertIsNone(sheet.Range("C1").Value2)

    def test_explicit_fixed_sum_writes_number_not_formula(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 매출 열 합계를 현재 합계값만 C1에 넣어줘",
                    session_id="sum-fixed",
                )

        self.assertTrue(result["success"])
        self.assertFalse(sheet.Range("C1").HasFormula)
        self.assertEqual(160, sheet.Range("C1").Value2)

    def test_average_supports_formula_and_missing_information_question(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                waiting = command_api.parse_command(
                    "엑셀에서 평균 내줘", session_id="average-clarification"
                )
                completed = command_api.parse_command(
                    "엑셀에서 A2:A4 평균을 C1에 넣어줘",
                    session_id="average-clarification",
                )

        self.assertEqual("clarification_required", waiting["status"])
        self.assertEqual(
            "missing_range", waiting["data"]["clarification"]["reason"]
        )
        self.assertTrue(completed["success"])
        self.assertEqual("=AVERAGE(A2:A4)", sheet.Range("C1").Formula)
        self.assertIn("평균", completed["message"])

    def test_ambiguous_format_asks_then_applies_selected_method(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 노란색으로 표시해줘",
                    session_id="format-choice",
                )
                before = sheet.Range("A3").Interior.ColorIndex
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "direct_format",
                    "format-choice",
                )

        self.assertEqual("clarification_required", first["status"])
        self.assertEqual(
            "multiple_possible_intents",
            first["data"]["confirmation"]["reason"],
        )
        self.assertEqual(
            "clarification",
            first["data"]["confirmation"]["request_kind"],
        )
        self.assertEqual(XL_NONE, before)
        self.assertTrue(result["success"])
        self.assertEqual(65535, sheet.Range("A3").Interior.Color)

    def test_explicit_conditional_format_runs_without_method_question(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 조건부 서식으로 노란색으로 표시해줘",
                    session_id="format-persistent",
                )

        self.assertTrue(result["success"])
        self.assertEqual(1, sheet.Range("A2:A4").FormatConditions.Count)

    def test_explicit_one_time_format_runs_without_method_question(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 지금만 빨간색으로 표시해줘",
                    session_id="format-direct",
                )

        self.assertTrue(result["success"])
        self.assertEqual(255, sheet.Range("A3").Interior.Color)
        self.assertEqual(255, sheet.Range("A4").Interior.Color)
        self.assertEqual(0, sheet.Range("A2:A4").FormatConditions.Count)

    def test_method_choice_cancel_changes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 노란색으로 표시해줘",
                    session_id="format-cancel",
                )
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "cancel",
                    "format-cancel",
                )

        self.assertEqual("cancelled", result["status"])
        self.assertEqual(XL_NONE, sheet.Range("A3").Interior.ColorIndex)
        self.assertEqual(0, sheet.Range("A2:A4").FormatConditions.Count)

    def test_duplicate_header_asks_for_column_before_sum(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            sheet.Range("B1").Value2 = "매출"
            sheet.Range("B2").Value2 = 1
            sheet.Range("B3").Value2 = 2
            sheet.Range("B4").Value2 = 3
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 매출 열 합계를 C1에 넣어줘",
                    session_id="duplicate-header",
                )
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "column_b",
                    "duplicate-header",
                )

        self.assertEqual("clarification_required", first["status"])
        self.assertEqual(
            "ambiguous_reference", first["data"]["confirmation"]["reason"]
        )
        self.assertEqual(
            "clarification", first["data"]["confirmation"]["request_kind"]
        )
        self.assertTrue(result["success"])
        self.assertEqual("=SUM(B2:B4)", sheet.Range("C1").Formula)

    def test_changed_values_after_method_question_require_reconfirmation(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-core-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 노란색으로 표시해줘",
                    session_id="format-recheck",
                )
                sheet.Range("A2").Value2 = 100
                second = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "direct_format",
                    "format-recheck",
                )
                before_apply = sheet.Range("A2").Interior.ColorIndex
                completed = command_api.resolve_confirmation(
                    second["data"]["confirmation"]["confirmation_id"],
                    "apply",
                    "format-recheck",
                )

        self.assertEqual("confirmation_required", second["status"])
        self.assertEqual("context_changed", second["data"]["confirmation"]["reason"])
        self.assertEqual(XL_NONE, before_apply)
        self.assertTrue(completed["success"])
        self.assertEqual(65535, sheet.Range("A2").Interior.Color)


if __name__ == "__main__":
    unittest.main()
