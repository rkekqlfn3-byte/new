"""Isolated live-COM probe for phase 6 Excel auxiliary actions."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import win32com.client
import win32process


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.app_actions.excel_adapter import ExcelAdapter


REPORT_PATH = os.path.join(ROOT, "verification", "phase6_excel_aux_report.json")


def cell_values(sheet, address):
    value = sheet.Range(address).Value2
    if isinstance(value, tuple):
        return [list(row) if isinstance(row, tuple) else row for row in value]
    return value


def main():
    application = None
    workbook = None
    report = {
        "phase": 6,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "isolated_excel_pid": None,
        "checks": [],
        "success": False,
    }
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.ScreenUpdating = False
        _, process_id = win32process.GetWindowThreadProcessId(int(application.Hwnd))
        report["isolated_excel_pid"] = int(process_id)
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Name = "Phase6"
        rows = (
            ("이름", "매출", "상태"),
            ("홍길동", 30, "진행"),
            ("김영희", 10, "완료"),
            ("홍길동", 20, "완료"),
        )
        sheet.Range("A1:C4").Value2 = rows
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            discovery_retry_delay=0.05,
        )

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
        assert result["verified"] and bool(sheet.Range("A2").Font.Bold)
        assert int(sheet.Range("C3").HorizontalAlignment) == -4108
        assert int(sheet.Range("B2").Interior.Color) == 65535
        report["checks"].append({"name": "format_range", "passed": True})

        prepared = adapter.prepare(
            "filter_range",
            {"column_name": "매출", "operator": ">=", "value": 20},
        )
        result = adapter.execute(prepared)
        assert result["verified"] and bool(sheet.FilterMode)
        report["checks"].append({"name": "filter_range", "passed": True})

        prepared = adapter.prepare("filter_range", {"clear": True})
        result = adapter.execute(prepared)
        assert result["verified"] and not bool(sheet.FilterMode)
        report["checks"].append({"name": "clear_filter", "passed": True})

        prepared = adapter.prepare(
            "find_replace",
            {
                "scope": "range",
                "range": "A2:A4",
                "find": "홍길동",
                "replace": "홍길순",
            },
        )
        result = adapter.execute(prepared)
        assert result["verified"]
        assert sheet.Range("A2").Value2 == "홍길순"
        assert sheet.Range("A4").Value2 == "홍길순"
        report["checks"].append({"name": "find_replace", "passed": True})

        prepared = adapter.prepare(
            "sort_range", {"column_name": "매출", "direction": "ascending"}
        )
        report["sort_before"] = cell_values(sheet, "A1:C4")
        report["sort_prepared_params"] = prepared.params
        result = adapter.execute(prepared)
        assert result["verified"]
        assert cell_values(sheet, "A2:C4") == [
            ["김영희", 10.0, "완료"],
            ["홍길순", 20.0, "완료"],
            ["홍길순", 30.0, "진행"],
        ]
        report["checks"].append({
            "name": "sort_whole_table_rows", "passed": True
        })
        report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if workbook is not None:
            try:
                report["sheet_at_error"] = cell_values(
                    workbook.Worksheets(1), "A1:C4"
                )
            except Exception:
                pass
        raise
    finally:
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        report["finished_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        with open(REPORT_PATH, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
