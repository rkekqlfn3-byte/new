"""Run phase-4 native actions in a disposable workbook on the active Excel instance."""

from __future__ import annotations

import gc
import json
import os
import sys
import time
from datetime import datetime

import psutil
import pythoncom
import win32com.client
import win32process

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from engine.app_actions.base import AppActionContextChanged
from engine.app_actions.excel_adapter import ExcelAdapter


def main():
    pythoncom.CoInitialize()
    application = None
    created_application = False
    scratch = None
    sheet = None
    report = {
        "phase": 4,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": False,
        "checks": {},
    }
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        created_application = True
        _, process_id = win32process.GetWindowThreadProcessId(
            int(application.Hwnd)
        )
        report["isolated_excel_pid"] = int(process_id)
        report["isolated_excel_instance"] = created_application
        report["user_excel_instance_attached"] = False
        scratch = application.Workbooks.Add()
        sheet = scratch.ActiveSheet
        sheet.Name = "JARVIS_Phase4"
        sheet.Range("A1").Value2 = "매출"
        sheet.Range("A2").Value2 = 40
        sheet.Range("A3").Value2 = 50
        sheet.Range("A4").Value2 = 70

        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            discovery_retry_delay=0.05,
            com_runtime=False,
        )

        prepared_sum = adapter.prepare(
            "sum_column_to_cell",
            {"column_name": "매출", "target_cell": "B1"},
        )
        sum_result = adapter.execute(prepared_sum)
        report["checks"]["sum_formula"] = {
            "verified": bool(sum_result.get("verified")),
            "formula": str(sheet.Range("B1").Formula),
            "value": sheet.Range("B1").Value2,
        }

        prepared_value = adapter.prepare(
            "sum_column_to_cell",
            {
                "source_range": "A2:A4",
                "target_cell": "C1",
                "result_mode": "value",
            },
        )
        value_result = adapter.execute(prepared_value)
        report["checks"]["sum_value"] = {
            "verified": bool(value_result.get("verified")),
            "value": sheet.Range("C1").Value2,
        }

        conditional = adapter.prepare(
            "apply_conditional_format",
            {
                "source_range": "A2:A4",
                "operator": "ge",
                "threshold": 50,
                "color": "yellow",
            },
        )
        conditional_result = adapter.execute(conditional)
        report["checks"]["conditional_format"] = {
            "verified": bool(conditional_result.get("verified")),
            "rule_count": int(sheet.Range("A2:A4").FormatConditions.Count),
        }

        direct = adapter.prepare(
            "format_matching_values",
            {
                "source_range": "A2:A4",
                "operator": "gt",
                "threshold": 50,
                "color": "red",
            },
        )
        direct_result = adapter.execute(direct)
        report["checks"]["direct_format"] = {
            "verified": bool(direct_result.get("verified")),
            "matching_count": direct.current_state.get("matching_count"),
            "a4_color": int(sheet.Range("A4").Interior.Color),
        }

        stale = adapter.prepare(
            "sum_column_to_cell",
            {"source_range": "A2:A4", "target_cell": "D1"},
        )
        sheet.Range("A2").Value2 = 41
        blocked = False
        try:
            adapter.execute(stale)
        except AppActionContextChanged:
            blocked = True
        report["checks"]["context_change_guard"] = {
            "blocked": blocked,
            "target_unchanged": sheet.Range("D1").Value2 is None,
        }

        report["success"] = all((
            report["checks"]["sum_formula"]["verified"],
            report["checks"]["sum_formula"]["formula"].upper() == "=SUM(A2:A4)",
            report["checks"]["sum_value"]["verified"],
            float(report["checks"]["sum_value"]["value"]) == 160.0,
            report["checks"]["conditional_format"]["verified"],
            report["checks"]["conditional_format"]["rule_count"] == 1,
            report["checks"]["direct_format"]["verified"],
            report["checks"]["direct_format"]["matching_count"] == 1,
            report["checks"]["context_change_guard"]["blocked"],
            report["checks"]["context_change_guard"]["target_unchanged"],
        ))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if scratch is not None:
            try:
                scratch.Close(SaveChanges=False)
            except Exception:
                pass
        if created_application and application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        scratch = None
        sheet = None
        gc.collect()
        isolated_pid = int(report.get("isolated_excel_pid") or 0)
        deadline = time.monotonic() + 5.0
        while isolated_pid and psutil.pid_exists(isolated_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        report["owned_process_cleanup_verified"] = bool(
            isolated_pid and not psutil.pid_exists(isolated_pid)
        )
        report["success"] = bool(
            report.get("success") and report["owned_process_cleanup_verified"]
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        pythoncom.CoUninitialize()
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
