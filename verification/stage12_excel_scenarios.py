"""Stage 12 live Excel scenarios in a disposable, dedicated COM instance.

The probe never attaches to an existing workbook.  It starts hidden Excel
instances with DispatchEx, writes only a temporary workbook, closes those
instances, and verifies that no process created by the probe is left behind.
"""

from __future__ import annotations

import gc
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import psutil
import pythoncom
import win32com.client
import win32process

from engine.app_actions.base import (
    AppActionAmbiguousTarget,
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
)
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.decision import DecisionEngine


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "stage12_excel_scenarios.json"


def excel_pids():
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                result.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def close_workbook(workbook):
    if workbook is None:
        return
    try:
        workbook.Close(SaveChanges=False)
    except Exception:
        pass


def quit_excel(application):
    if application is None:
        return
    try:
        application.DisplayAlerts = False
        application.Quit()
    except Exception:
        pass


def run_regression_case(name):
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    return {
        "passed": result.wasSuccessful(),
        "ran": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }


def main():
    baseline_pids = excel_pids()
    temp_dir = tempfile.mkdtemp(prefix="jarvis-stage12-excel-")
    workbook_path = os.path.join(temp_dir, "stage12_scenarios.xlsx")
    application = None
    workbook = None
    owned_pids = set()
    scenarios = []
    failures = []
    started = time.perf_counter()

    def record(number, name, passed, *, mode="live_com", detail=None):
        item = {
            "number": number,
            "name": name,
            "mode": mode,
            "passed": bool(passed),
        }
        if detail is not None:
            item["detail"] = detail
        scenarios.append(item)
        if not passed:
            failures.append(name)

    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        application.ScreenUpdating = False
        _, primary_pid = win32process.GetWindowThreadProcessId(int(application.Hwnd))
        owned_pids.add(int(primary_pid))
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Name = "Stage12"
        rows = (
            ("이름", "매출", "상태"),
            ("홍길동", 30, "진행"),
            ("김영희", 10, "완료"),
            ("박자비스", 20, "완료"),
        )
        sheet.Range("A1:C4").Value2 = rows
        workbook.SaveAs(workbook_path, FileFormat=51)
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            discovery_retry_delay=0.05,
        )

        result = adapter.execute(adapter.prepare(
            "write_cell", {"cell": "E1", "value": "실제 값"}
        ))
        record(1, "셀 값 입력", result["verified"] and sheet.Range("E1").Value2 == "실제 값")

        result = adapter.execute(adapter.prepare(
            "write_cell", {"cell": "E2", "value": "=SUM(B2:B4)"}
        ))
        record(2, "수식 입력", result["verified"] and str(sheet.Range("E2").Formula).upper() == "=SUM(B2:B4)")

        result = adapter.execute(adapter.prepare(
            "sum_column_to_cell",
            {"source_range": "B2:B4", "target_cell": "E3", "result_mode": "value"},
        ))
        record(3, "고정값 합계 입력", result["verified"] and float(sheet.Range("E3").Value2) == 60.0)

        result = adapter.execute(adapter.prepare(
            "apply_conditional_format",
            {"source_range": "B2:B4", "operator": "ge", "threshold": 20, "color": "yellow"},
        ))
        record(4, "조건부서식", result["verified"] and int(sheet.Range("B2:B4").FormatConditions.Count) == 1)

        result = adapter.execute(adapter.prepare(
            "format_matching_values",
            {"source_range": "B2:B4", "operator": "ge", "threshold": 20, "color": "red"},
        ))
        record(5, "직접 색칠", result["verified"] and int(sheet.Range("B2").Interior.Color) == 255 and int(sheet.Range("B4").Interior.Color) == 255)

        result = adapter.execute(adapter.prepare(
            "format_range",
            {"range": "A2:C3", "bold": True, "font_size": 13, "alignment": "center", "fill_color": "yellow"},
        ))
        record(6, "범위 서식", result["verified"] and bool(sheet.Range("A2").Font.Bold) and int(sheet.Range("C3").HorizontalAlignment) == -4108)

        result = adapter.execute(adapter.prepare(
            "sort_range", {"table_range": "A1:C4", "column_name": "매출", "direction": "ascending"}
        ))
        record(7, "정렬", result["verified"] and list(sheet.Range("B2:B4").Value2) == [(10.0,), (20.0,), (30.0,)])

        filtered = adapter.execute(adapter.prepare(
            "filter_range", {"table_range": "A1:C4", "column_name": "매출", "operator": "ge", "value": 20}
        ))
        cleared = adapter.execute(adapter.prepare("filter_range", {"clear": True}))
        record(8, "필터·필터 해제", filtered["verified"] and cleared["verified"] and not bool(sheet.FilterMode))

        sheet.Range("F1:F3").Value2 = (("대기",), ("완료",), ("대기",))
        result = adapter.execute(adapter.prepare(
            "find_replace", {"scope": "range", "range": "F1:F3", "find": "대기", "replace": "진행"}
        ))
        record(9, "찾기·바꾸기", result["verified"] and sheet.Range("F1").Value2 == "진행" and sheet.Range("F3").Value2 == "진행")

        workbook.Save()
        read_app = None
        read_book = None
        read_sheet = None
        try:
            # A second dedicated process opens the file while the writer owns
            # it, guaranteeing a real read-only workbook instead of relying on
            # an Excel version's same-instance Open(ReadOnly=True) behavior.
            read_app = win32com.client.DispatchEx("Excel.Application")
            read_app.Visible = False
            read_app.DisplayAlerts = False
            _, read_pid = win32process.GetWindowThreadProcessId(int(read_app.Hwnd))
            owned_pids.add(int(read_pid))
            read_book = read_app.Workbooks.Open(
                workbook_path, ReadOnly=True, AddToMru=False
            )
            read_sheet = read_book.Worksheets("Stage12")
            workbook_read_only = bool(read_book.ReadOnly)
            read_adapter = ExcelAdapter(
                application_getter=lambda: read_app,
                process_counter=lambda: 1,
                discovery_retry_delay=0.05,
            )
            blocked = False
            try:
                read_adapter.prepare("write_cell", {"cell": "G1", "value": "차단"})
            except AppActionBlocked:
                blocked = True
            read_only_value = read_sheet.Range("G1").Value2
        finally:
            read_sheet = None
            close_workbook(read_book)
            read_book = None
            quit_excel(read_app)
            read_app = None
            read_adapter = None
            gc.collect()
        record(
            10,
            "읽기 전용 차단",
            workbook_read_only and blocked and read_only_value in {None, ""},
            detail={
                "workbook_read_only": workbook_read_only,
                "blocked": blocked,
                "target_value": read_only_value,
            },
        )

        sheet.Protect()
        blocked = False
        try:
            adapter.prepare("write_cell", {"cell": "G1", "value": "차단"})
        except AppActionBlocked:
            blocked = True
        record(11, "보호된 시트 차단", blocked and sheet.Range("G1").Value2 is None)
        sheet.Unprotect()

        sheet.Range("D1").Value2 = "매출"
        ambiguous = False
        try:
            adapter.prepare("sum_column_to_cell", {"column_name": "매출", "target_cell": "G2"})
        except AppActionAmbiguousTarget as error:
            ambiguous = len(error.candidates) == 2
        record(12, "중복 열 제목", ambiguous and sheet.Range("G2").Value2 is None)
        sheet.Range("D1:D4").ClearContents()

        sheet.Range("J1:K1").Merge()
        blocked = False
        try:
            adapter.prepare("write_cell", {"cell": "J1", "value": "차단"})
        except AppActionBlocked:
            blocked = True
        record(13, "병합 셀", blocked and sheet.Range("J1").Value2 is None)
        sheet.Range("J1:K1").UnMerge()

        blocked = False
        before = sheet.Range("B3").Value2
        try:
            adapter.prepare(
                "sum_column_to_cell",
                {"source_range": "B2:B4", "target_cell": "B3", "result_mode": "value"},
            )
        except AppActionBlocked:
            blocked = True
        record(14, "출력 셀이 원본 범위에 포함", blocked and sheet.Range("B3").Value2 == before)

        second_app = None
        second_book = None
        try:
            second_app = win32com.client.DispatchEx("Excel.Application")
            second_app.Visible = False
            second_app.DisplayAlerts = False
            _, second_pid = win32process.GetWindowThreadProcessId(int(second_app.Hwnd))
            owned_pids.add(int(second_pid))
            second_book = second_app.Workbooks.Add()
            multi_adapter = ExcelAdapter(
                application_getter=lambda: application,
                process_counter=lambda: 2,
                discovery_retry_delay=0.05,
            )
            blocked = False
            try:
                multi_adapter.prepare("write_cell", {"cell": "G3", "value": "차단"})
            except AppActionBlocked:
                blocked = True
            record(15, "여러 Excel 인스턴스", blocked and sheet.Range("G3").Value2 is None)
        finally:
            close_workbook(second_book)
            quit_excel(second_app)
            second_book = None
            second_app = None

        sheet.Range("G4").Value2 = "처음"
        prepared = adapter.prepare("write_cell", {"cell": "G4", "value": "최종"})
        sheet.Range("G4").Value2 = "중간 변경"
        changed_blocked = False
        try:
            adapter.execute(prepared)
        except AppActionContextChanged:
            changed_blocked = True
        record(16, "확인 대기 중 문서 변경", changed_blocked and sheet.Range("G4").Value2 == "중간 변경")

        sheet.Range("H1").Value2 = "유지"
        prepared = adapter.prepare("write_cell", {"cell": "H1", "value": "취소값"})
        decision = DecisionEngine().evaluate(prepared)
        record(17, "취소", decision.requires_confirmation and sheet.Range("H1").Value2 == "유지")

        sheet.Range("H2").Value2 = "롤백 원본"
        prepared = adapter.prepare("write_cell", {"cell": "H2", "value": "실패값"})
        rolled_back = False
        with mock.patch.object(
            adapter,
            "_verify_written_cell",
            side_effect=AppActionVerificationError("주입된 검증 실패"),
        ):
            try:
                adapter.execute(prepared)
            except AppActionVerificationError:
                rolled_back = sheet.Range("H2").Value2 == "롤백 원본"
        record(18, "롤백", rolled_back)

        learning = run_regression_case(
            "tests.integration.test_learning_loop.LearningLoopTests."
            "test_approved_sentence_runs_locally_next_time_with_saved_target"
        )
        record(19, "학습 매크로 재실행", learning["passed"], mode="isolated_integration", detail=learning)

        fallback_names = [
            "tests.integration.test_skill_fallback.SkillExecutorFallbackTests."
            "test_target_not_found_before_mutation_allows_one_fallback",
            "tests.integration.test_skill_fallback.SkillExecutorFallbackTests."
            "test_partial_execution_blocks_fallback",
        ]
        fallback_results = [run_regression_case(name) for name in fallback_names]
        record(20, "fallback 허용·금지", all(item["passed"] for item in fallback_results), mode="isolated_failure_injection", detail=fallback_results)

    except Exception as error:
        failures.append(f"probe_error:{type(error).__name__}")
        probe_error = f"{type(error).__name__}: {error}"
        try:
            error_sheet_snapshot = {
                "headers": [sheet.Cells(1, column).Value2 for column in range(1, 6)],
                "table": [
                    [sheet.Cells(row, column).Value2 for column in range(1, 4)]
                    for row in range(1, 5)
                ],
            }
        except Exception:
            error_sheet_snapshot = None
    else:
        probe_error = None
    finally:
        close_workbook(workbook)
        quit_excel(application)
        workbook = None
        application = None
        sheet = None
        adapter = None
        prepared = None
        gc.collect()
        pythoncom.CoUninitialize()
        shutil.rmtree(temp_dir, ignore_errors=True)

    deadline = time.time() + 8
    while time.time() < deadline and (excel_pids() & (owned_pids - baseline_pids)):
        time.sleep(0.1)
    graceful_leftovers = sorted(excel_pids() & (owned_pids - baseline_pids))
    forced_cleanup = []
    for process_id in graceful_leftovers:
        try:
            process = psutil.Process(process_id)
            if process.name().casefold() != "excel.exe":
                continue
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            forced_cleanup.append(process_id)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    leftovers = sorted(excel_pids() & (owned_pids - baseline_pids))
    if not leftovers:
        shutil.rmtree(temp_dir, ignore_errors=True)
    report = {
        "stage": 12,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": len(scenarios) == 20 and not failures and not leftovers,
        "isolation": {
            "dispatch_ex_only": True,
            "baseline_excel_pids_preserved": True,
            "owned_excel_pids": sorted(owned_pids),
            "graceful_exit_delays": graceful_leftovers,
            "forced_cleanup_pids": forced_cleanup,
            "owned_processes_left": leftovers,
            "temporary_directory_removed": not os.path.exists(temp_dir),
        },
        "scenario_count": len(scenarios),
        "passed_count": sum(1 for item in scenarios if item["passed"]),
        "scenarios": scenarios,
        "failures": failures,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    if probe_error:
        report["error"] = probe_error
        if error_sheet_snapshot is not None:
            report["error_sheet_snapshot"] = error_sheet_snapshot
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
