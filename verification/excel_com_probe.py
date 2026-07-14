"""Isolated Excel COM smoke test for source and frozen Jarvis runtimes.

The probe always creates a dedicated, invisible Excel instance with DispatchEx
and only writes a workbook inside a unique temporary directory. It never
attaches to or modifies a workbook already opened by the user.
"""

from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import time
import uuid


def _safe_close(workbook):
    if workbook is None:
        return
    try:
        workbook.Close(SaveChanges=False)
    except Exception:
        pass


def _safe_quit(application):
    if application is None:
        return
    try:
        application.DisplayAlerts = False
        application.Quit()
    except Exception:
        pass


def run_excel_probe():
    """Run an isolated write/save/reopen/read cycle and return JSON-safe data."""
    import pythoncom
    import win32com.client

    started = time.monotonic()
    marker = f"JARVIS_EXCEL_PROBE_{uuid.uuid4().hex}"
    application = None
    workbook = None
    reopened = None
    sheet = None
    reopened_sheet = None
    temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-excel-probe-")
    workbook_path = os.path.join(temp_dir.name, "jarvis_excel_probe.xlsx")
    stage = "initialize_com"
    pythoncom.CoInitialize()
    try:
        stage = "start_excel"
        # DispatchEx must create a dedicated instance instead of attaching to a
        # workbook the user may already have open.
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        application.EnableEvents = False
        application.ScreenUpdating = False

        stage = "create_workbook"
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Name = "JarvisProbe"

        stage = "write_cells"
        sheet.Range("A1").Value2 = marker
        sheet.Range("A2").Value2 = 10
        sheet.Range("A3").Value2 = 20
        sheet.Range("A4").Formula = "=SUM(A2:A3)"
        application.CalculateFull()

        initial_marker = str(sheet.Range("A1").Value2 or "")
        initial_sum = sheet.Range("A4").Value2
        initial_formula = str(sheet.Range("A4").Formula or "")
        if initial_marker != marker:
            raise RuntimeError("셀에 쓴 표식이 즉시 읽은 값과 다릅니다.")
        if float(initial_sum) != 30.0:
            raise RuntimeError(f"SUM 계산 결과가 30이 아닙니다: {initial_sum!r}")

        stage = "save_workbook"
        workbook.SaveAs(workbook_path, FileFormat=51)
        if not os.path.isfile(workbook_path):
            raise RuntimeError("임시 XLSX 파일이 저장되지 않았습니다.")
        _safe_close(workbook)
        workbook = None
        sheet = None

        stage = "reopen_workbook"
        reopened = application.Workbooks.Open(
            workbook_path,
            UpdateLinks=0,
            ReadOnly=True,
            AddToMru=False,
        )
        reopened_sheet = reopened.Worksheets("JarvisProbe")
        reopened_marker = str(reopened_sheet.Range("A1").Value2 or "")
        reopened_sum = reopened_sheet.Range("A4").Value2
        reopened_formula = str(reopened_sheet.Range("A4").Formula or "")
        if reopened_marker != marker:
            raise RuntimeError("재개봉한 파일의 표식이 원래 값과 다릅니다.")
        if float(reopened_sum) != 30.0:
            raise RuntimeError(
                f"재개봉한 파일의 SUM 결과가 30이 아닙니다: {reopened_sum!r}"
            )
        if "SUM(A2:A3)" not in reopened_formula.upper().replace("$", ""):
            raise RuntimeError(
                f"재개봉한 파일의 수식이 예상과 다릅니다: {reopened_formula!r}"
            )

        return {
            "success": True,
            "stage": "complete",
            "excel_version": str(application.Version),
            "marker_round_trip": True,
            "formula_round_trip": initial_formula == reopened_formula,
            "calculated_value": float(reopened_sum),
            "saved_and_reopened": True,
            "dedicated_instance": True,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }
    except Exception as error:
        return {
            "success": False,
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }
    finally:
        reopened_sheet = None
        _safe_close(reopened)
        reopened = None
        sheet = None
        _safe_close(workbook)
        workbook = None
        _safe_quit(application)
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        temp_dir.cleanup()


def main():
    result = run_excel_probe()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
