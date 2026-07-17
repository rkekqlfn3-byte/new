import json
import os
import tempfile
import unittest
from unittest import mock

from engine.action_executor import ActionExecutor, ActionPlanError
from engine.api import command_api
from engine.app_actions.base import (
    AppActionBlocked,
    AppActionBusy,
    AppActionContextChanged,
    AppActionVerificationError,
)
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.registry import AppActionRegistry
from engine.app_actions.value_normalizer import (
    excel_formulas_equal,
    normalize_cell_address,
    normalize_excel_input,
)
from engine.decision import DecisionEngine
from engine.execution_runtime import ExecutionController
from engine.parser import CommandParser


class FakeCell:
    def __init__(self, value=None, formula=None, merged=False):
        self._value = value
        self._formula = formula
        self.MergeCells = merged
        self.CountLarge = 1

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

    def Address(self, *_args):
        return self.address


class FakeSheet:
    Type = -4167

    def __init__(self, name="Sheet1", protected=False):
        self.Name = name
        self.ProtectContents = protected
        self.cells = {}

    def Range(self, address):
        address = normalize_cell_address(address)
        cell = self.cells.setdefault(address, FakeCell())
        cell.address = address
        return cell


class DelayedNormalizedFormulaCell(FakeCell):
    def __init__(self):
        super().__init__()
        self.formula_set_count = 0
        self.verification_reads = 0

    @property
    def HasFormula(self):
        if self._formula is None:
            return False
        self.verification_reads += 1
        return self.verification_reads >= 3

    @property
    def Formula(self):
        return self._formula if self._formula is not None else self._value

    @Formula.setter
    def Formula(self, value):
        self.formula_set_count += 1
        self.verification_reads = 0
        self._formula = "=sum( B1 , 7 )"
        self._value = 107


class FakeWorkbook:
    def __init__(self, sheet, name="Stage3.xlsx", read_only=False):
        self.Name = name
        self.Path = "C:\\JarvisTests"
        self.FullName = os.path.join(self.Path, name)
        self.ReadOnly = read_only
        self.sheet = sheet


class FakeExcel:
    def __init__(self, sheet=None, workbook=None):
        self.Hwnd = 12345
        self.ActiveSheet = sheet or FakeSheet()
        self.ActiveWorkbook = workbook or FakeWorkbook(self.ActiveSheet)


class BusyExcel:
    Hwnd = 12345

    def __init__(self, failures):
        self.sheet = FakeSheet()
        self.workbook = FakeWorkbook(self.sheet)
        self.failures = failures
        self.attempts = 0

    @property
    def ActiveWorkbook(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise AttributeError("Excel.Application.ActiveWorkbook")
        return self.workbook

    @property
    def ActiveSheet(self):
        return self.sheet


def adapter_for(application):
    return ExcelAdapter(
        application_getter=lambda: application,
        process_counter=lambda: 1,
        discovery_retry_delay=0,
    )


class ExcelValueNormalizerTests(unittest.TestCase):
    def test_cell_and_values_are_normalized_without_precision_loss(self):
        self.assertEqual("XFD1048576", normalize_cell_address("$xfd$1048576"))
        self.assertEqual(
            {"kind": "value", "value": 1000},
            normalize_excel_input("1,000"),
        )
        self.assertEqual(
            {"kind": "value", "value": "1234567890123456"},
            normalize_excel_input("1234567890123456"),
        )
        self.assertEqual(
            {"kind": "formula", "value": "=SUM(A1:A3)"},
            normalize_excel_input("=SUM(A1:A3)"),
        )
        self.assertTrue(
            excel_formulas_equal("=sum( B1 , 7 )", "=SUM(B1,7)")
        )
        self.assertFalse(excel_formulas_equal('="ABC"', '="abc"'))
        with self.assertRaises(AppActionBlocked):
            normalize_cell_address("A1:B2")


class ExcelAdapterTests(unittest.TestCase):
    def test_blank_cell_is_prepared_written_and_verified(self):
        excel = FakeExcel()
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "write_cell", {"cell": "B2", "value": "100", "value_type": "auto"}
        )
        decision = DecisionEngine().evaluate(prepared)
        result = adapter.execute(prepared)

        self.assertEqual("execute", decision.decision)
        self.assertFalse(prepared.destructive)
        self.assertTrue(result["verified"])
        self.assertTrue(result["changed"])
        self.assertEqual(100, excel.ActiveSheet.Range("B2").Value2)
        json.dumps(prepared.to_dict(), ensure_ascii=False)

    def test_existing_cell_requires_confirmation(self):
        excel = FakeExcel()
        excel.ActiveSheet.Range("A1").Value2 = "기존 값"
        prepared = adapter_for(excel).prepare(
            "write_cell", {"cell": "A1", "value": "새 값"}
        )
        decision = DecisionEngine().evaluate(prepared)

        self.assertTrue(prepared.destructive)
        self.assertTrue(decision.requires_confirmation)
        self.assertEqual("destructive_action", decision.reason)
        self.assertEqual("기존 값", excel.ActiveSheet.Range("A1").Value2)

    def test_verified_write_can_restore_original_cell_snapshot(self):
        excel = FakeExcel()
        excel.ActiveSheet.Range("A1").Value2 = "원래 값"
        adapter = adapter_for(excel)
        prepared = adapter.prepare("write_cell", {"cell": "A1", "value": "수정 값"})
        result = adapter.execute(prepared)

        restored = adapter.undo(prepared, {"after_observations": result})

        self.assertTrue(restored["verified"])
        self.assertEqual("원래 값", excel.ActiveSheet.Range("A1").Value2)

    def test_context_change_blocks_stale_prepared_action(self):
        excel = FakeExcel()
        excel.ActiveSheet.Range("A1").Value2 = "기존"
        adapter = adapter_for(excel)
        prepared = adapter.prepare("write_cell", {"cell": "A1", "value": "새 값"})
        excel.ActiveSheet.Range("A1").Value2 = "사용자가 바꿈"

        with self.assertRaises(AppActionContextChanged):
            adapter.execute(prepared)
        self.assertEqual("사용자가 바꿈", excel.ActiveSheet.Range("A1").Value2)

    def test_formula_is_written_through_formula_property(self):
        excel = FakeExcel()
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "write_cell", {"cell": "C1", "value": "=SUM(A1:A3)"}
        )
        result = adapter.execute(prepared)

        self.assertEqual("=SUM(A1:A3)", excel.ActiveSheet.Range("C1").Formula)
        self.assertEqual("read_formula", result["verification_method"])

    def test_formula_write_is_not_repeated_while_readback_settles(self):
        excel = FakeExcel()
        delayed = DelayedNormalizedFormulaCell()
        excel.ActiveSheet.cells["C1"] = delayed
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "write_cell", {"cell": "C1", "value": "=SUM(B1,7)"}
        )
        result = adapter.execute(prepared)

        self.assertTrue(result["verified"])
        self.assertEqual(1, delayed.formula_set_count)
        self.assertGreaterEqual(delayed.verification_reads, 3)

    def test_rollback_failure_never_turns_a_verification_failure_into_success(self):
        class RestoreFailureCell:
            def __init__(self):
                self._value = "원본"
                self.write_count = 0

            @property
            def Value2(self):
                return self._value

            @Value2.setter
            def Value2(self, value):
                self.write_count += 1
                if self.write_count > 1 and value == "원본":
                    raise OSError("주입된 롤백 실패")
                self._value = value

        excel = FakeExcel()
        adapter = adapter_for(excel)
        prepared = adapter.prepare(
            "write_cell", {"cell": "B1", "value": "새 값"}
        )
        cell = RestoreFailureCell()
        with mock.patch.object(
            adapter,
            "_verify_written_cell",
            side_effect=AppActionVerificationError("주입된 검증 실패"),
        ):
            with self.assertRaises(AppActionVerificationError):
                adapter._write_and_verify(
                    cell,
                    {"current_formula": None, "current_value": "원본"},
                    {"kind": "value", "value": "새 값"},
                    prepared,
                )

        self.assertEqual("새 값", cell.Value2)

    def test_read_only_protected_and_merged_targets_are_blocked(self):
        read_only_excel = FakeExcel()
        read_only_excel.ActiveWorkbook.ReadOnly = True
        with self.assertRaises(AppActionBlocked):
            adapter_for(read_only_excel).prepare(
                "write_cell", {"cell": "A1", "value": "x"}
            )

        protected_excel = FakeExcel(sheet=FakeSheet(protected=True))
        protected_excel.ActiveWorkbook = FakeWorkbook(protected_excel.ActiveSheet)
        with self.assertRaises(AppActionBlocked):
            adapter_for(protected_excel).prepare(
                "write_cell", {"cell": "A1", "value": "x"}
            )

        merged_excel = FakeExcel()
        merged_excel.ActiveSheet.cells["A1"] = FakeCell(merged=True)
        with self.assertRaises(AppActionBlocked):
            adapter_for(merged_excel).prepare(
                "write_cell", {"cell": "A1", "value": "x"}
            )

    def test_busy_excel_discovery_retries_reads_but_returns_clear_final_error(self):
        temporarily_busy = BusyExcel(failures=2)
        adapter = ExcelAdapter(
            application_getter=lambda: temporarily_busy,
            process_counter=lambda: 1,
            discovery_attempts=3,
            discovery_retry_delay=0,
        )
        prepared = adapter.prepare(
            "write_cell", {"cell": "A1", "value": "재시도 성공"}
        )
        self.assertEqual("A1", prepared.target)
        self.assertEqual(3, temporarily_busy.attempts)

        always_busy = BusyExcel(failures=10)
        blocked = ExcelAdapter(
            application_getter=lambda: always_busy,
            process_counter=lambda: 1,
            discovery_attempts=3,
            discovery_retry_delay=0,
        )
        with self.assertRaises(AppActionBusy) as raised:
            blocked.prepare("write_cell", {"cell": "A1", "value": "실행 안 함"})
        self.assertIn("Enter 또는 Esc", str(raised.exception))
        self.assertIsNone(always_busy.sheet.Range("A1").Value2)


class PreparedActionPlanBoundaryTests(unittest.TestCase):
    def test_app_command_requires_prepared_action_before_earlier_side_effect(self):
        registry = AppActionRegistry({"excel": adapter_for(FakeExcel())})
        executor = ActionExecutor(
            {"메모장": "notepad"}, app_action_registry=registry
        )
        with mock.patch.object(executor, "_open_app") as open_app:
            with self.assertRaises(ActionPlanError):
                executor.execute_plan([
                    {"action": "open_app", "target": "메모장"},
                    {
                        "action": "app_command",
                        "target": "excel",
                        "operation": "write_cell",
                        "params": {"cell": "A1", "value": "100"},
                    },
                ])
        open_app.assert_not_called()

    def test_recursive_app_params_slots_render_only_after_validation(self):
        excel = FakeExcel()
        registry = AppActionRegistry({"excel": adapter_for(excel)})
        prepared = registry.prepare(
            "excel", "write_cell", {"cell": "B1", "value": "200"}
        )
        executor = ActionExecutor({}, app_action_registry=registry)
        result = executor.execute_plan(
            [{
                "action": "app_command",
                "target": "excel",
                "operation": "write_cell",
                "params": {"cell": "{cell}", "value": "{value}"},
            }],
            {"cell": "B1", "value": "200"},
            prepared_app_actions={1: prepared},
        )
        self.assertTrue(result["verified"])
        self.assertEqual(200, excel.ActiveSheet.Range("B1").Value2)


class ExcelParserConfirmationFlowTests(unittest.TestCase):
    @staticmethod
    def _parser(temp_dir, excel):
        parser = CommandParser()
        parser.execution_controller = ExecutionController(
            os.path.join(temp_dir, "diagnostics.json")
        )
        parser.app_action_registry = AppActionRegistry(
            {"excel": adapter_for(excel)}
        )
        parser.action_executor.controller = parser.execution_controller
        parser.action_executor.app_action_registry = parser.app_action_registry
        parser.macro_runner.controller = parser.execution_controller
        return parser

    def test_local_excel_command_writes_blank_cell_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-write-") as temp_dir:
            excel = FakeExcel()
            parser = self._parser(temp_dir, excel)
            parser.llm_engine.process_command = mock.Mock(
                side_effect=AssertionError("AI should not run")
            )
            with mock.patch.object(command_api, "parser", parser):
                result = command_api.parse_command(
                    "엑셀 B1에 100을 입력해줘", session_id="excel-a"
                )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("app_command", result["action"])
        self.assertEqual(100, excel.ActiveSheet.Range("B1").Value2)
        parser.llm_engine.process_command.assert_not_called()

    def test_existing_cell_changes_only_after_one_shot_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-write-") as temp_dir:
            excel = FakeExcel()
            excel.ActiveSheet.Range("A1").Value2 = "기존"
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 A1에 새 값을 입력해줘", session_id="excel-b"
                )
                confirmation_id = first["data"]["confirmation"]["confirmation_id"]
                before = excel.ActiveSheet.Range("A1").Value2
                completed = command_api.resolve_confirmation(
                    confirmation_id, "write", "excel-b"
                )
                duplicate = command_api.resolve_confirmation(
                    confirmation_id, "write", "excel-b"
                )

        self.assertEqual("기존", before)
        self.assertEqual("confirmation_required", first["status"])
        self.assertTrue(completed["success"])
        self.assertEqual("새 값", excel.ActiveSheet.Range("A1").Value2)
        self.assertEqual("confirmation_already_consumed", duplicate["status"])

    def test_changed_cell_is_reconfirmed_before_execution(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-write-") as temp_dir:
            excel = FakeExcel()
            excel.ActiveSheet.Range("A1").Value2 = "처음"
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 A1에 최종 값을 입력해줘", session_id="excel-c"
                )
                first_id = first["data"]["confirmation"]["confirmation_id"]
                excel.ActiveSheet.Range("A1").Value2 = "중간에 변경"
                reconfirm = command_api.resolve_confirmation(
                    first_id, "write", "excel-c"
                )
                unchanged_before_second = excel.ActiveSheet.Range("A1").Value2
                second_id = reconfirm["data"]["confirmation"]["confirmation_id"]
                completed = command_api.resolve_confirmation(
                    second_id, "write", "excel-c"
                )

        self.assertEqual("confirmation_required", reconfirm["status"])
        self.assertEqual("context_changed", reconfirm["data"]["confirmation"]["reason"])
        self.assertNotEqual(first_id, second_id)
        self.assertEqual("중간에 변경", unchanged_before_second)
        self.assertTrue(completed["success"])
        self.assertEqual("최종 값", excel.ActiveSheet.Range("A1").Value2)

    def test_cancel_leaves_existing_cell_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-excel-write-") as temp_dir:
            excel = FakeExcel()
            excel.ActiveSheet.Range("A1").Value2 = "유지"
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    "엑셀 A1에 바꿀 값을 입력해줘", session_id="excel-d"
                )
                result = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "cancel",
                    "excel-d",
                )

        self.assertEqual("cancelled", result["status"])
        self.assertEqual("유지", excel.ActiveSheet.Range("A1").Value2)


if __name__ == "__main__":
    unittest.main()
