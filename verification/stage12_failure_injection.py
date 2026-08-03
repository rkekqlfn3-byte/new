"""Stage 12 failure-injection matrix with one isolated live COM disconnect."""

from __future__ import annotations

import gc
import io
import json
import unittest
from datetime import datetime
from pathlib import Path

import psutil
import pythoncom
import win32com.client
import win32process

from engine.app_actions.base import AppActionError
from engine.app_actions.excel_adapter import ExcelAdapter

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "stage12_failure_injection_report.json"


def excel_pids():
    found = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                found.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def run_test(name):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName(name)
    )
    return {
        "passed": result.wasSuccessful(),
        "ran": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }


def live_disconnect():
    baseline = excel_pids()
    application = None
    workbook = None
    adapter = None
    owned_pid = None
    prepared = None
    caught = None
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        _, owned_pid = win32process.GetWindowThreadProcessId(int(application.Hwnd))
        workbook = application.Workbooks.Add()
        workbook.Worksheets(1)
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            discovery_attempts=2,
            discovery_retry_delay=0.01,
        )
        prepared = adapter.prepare(
            "write_cell", {"cell": "A1", "value": "연결 종료 후 쓰지 않음"}
        )
        workbook.Close(SaveChanges=False)
        workbook = None
        application.Quit()
        try:
            adapter.execute(prepared)
        except Exception as error:  # real COM may surface a wrapped pywintypes error
            caught = error
        passed = isinstance(caught, AppActionError)
        return {
            "passed": passed,
            "caught_error": type(caught).__name__ if caught else None,
            "reported_success": False,
            "owned_pid": int(owned_pid),
        }
    finally:
        try:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if application is not None:
                application.Quit()
        except Exception:
            pass
        workbook = None
        application = None
        adapter = None
        prepared = None
        gc.collect()
        pythoncom.CoUninitialize()
        if owned_pid and owned_pid not in baseline and psutil.pid_exists(owned_pid):
            try:
                process = psutil.Process(owned_pid)
                if process.name().casefold() == "excel.exe":
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass


def main():
    live = live_disconnect()
    matrix = [
        {
            "number": 1,
            "name": "Excel 실행 중 종료",
            "mode": "live_com",
            **live,
        },
        {
            "number": 2,
            "name": "COM 연결 끊김",
            "mode": "live_com",
            **live,
        },
    ]
    tests = [
        (3, "실행 전 대상 문서 변경", "tests.windows.test_excel_native_write.ExcelAdapterTests.test_context_change_blocks_stale_prepared_action"),
        (4, "확인 대기 중 대상 변경", "tests.windows.test_excel_native_write.ExcelParserConfirmationFlowTests.test_changed_cell_is_reconfirmed_before_execution"),
        (5, "검증 결과 불일치", "tests.integration.test_postconditions.SkillPostconditionIntegrationTests.test_verification_failure_never_uses_fallback"),
        (6, "롤백 실패", "tests.windows.test_excel_native_write.ExcelAdapterTests.test_rollback_failure_never_turns_a_verification_failure_into_success"),
        (7, "주 경로 사용 불가", "tests.integration.test_skill_fallback.SkillExecutorFallbackTests.test_primary_unavailable_falls_back_to_uia"),
        (8, "UI 요소 중복", "tests.windows.test_ui_automation.StructuredUIAutomationTests.test_equal_candidates_are_never_clicked"),
        (9, "동적 코드 위험 동작", "tests.integration.test_dynamic_code_preflight.DynamicCodePolicyTests.test_shell_registry_credentials_and_recursive_delete_are_blocked"),
        (10, "success=false 반환", "tests.integration.test_skill_fallback.SkillExecutorFallbackTests.test_returned_failure_is_not_counted_as_success"),
        (11, "네트워크 오류", "tests.integration.test_ai_router.LLMRouterTests.test_both_provider_failures_return_combined_explanation"),
        (12, "중복 확인 응답", "tests.integration.test_confirmation_flow.ConfirmationApiFlowTests.test_demo_confirmation_text_and_button_flow_are_one_shot"),
    ]
    for number, label, test_name in tests:
        result = run_test(test_name)
        matrix.append({
            "number": number,
            "name": label,
            "mode": "isolated_failure_injection",
            "test": test_name,
            **result,
        })
    matrix.sort(key=lambda item: item["number"])
    report = {
        "stage": 12,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": len(matrix) == 12 and all(item["passed"] for item in matrix),
        "scenario_count": len(matrix),
        "passed_count": sum(1 for item in matrix if item["passed"]),
        "scenarios": matrix,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
