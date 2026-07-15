"""Run phase-4 native actions in a disposable workbook on the active Excel instance."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pythoncom
import win32com.client

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from engine.app_actions.base import AppActionContextChanged
from engine.app_actions.excel_adapter import ExcelAdapter


def main():
    pythoncom.CoInitialize()
    application = None
    created_application = False
    original_workbook = None
    scratch = None
    report = {
        "phase": 4,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": False,
        "checks": {},
    }
    try:
        try:
            application = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            application = win32com.client.DispatchEx("Excel.Application")
            application.Visible = False
            application.DisplayAlerts = False
            created_application = True
        report["isolated_excel_instance"] = created_application
        original_workbook = application.ActiveWorkbook
        report["original_workbook"] = (
            str(original_workbook.Name) if original_workbook is not None else None
        )
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
        if original_workbook is not None:
            try:
                original_workbook.Activate()
            except Exception:
                pass
        if created_application and application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
