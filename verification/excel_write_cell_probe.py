"""Isolated live-COM verification for the phase 3 Excel write_cell slice."""

from __future__ import annotations

import gc
import json
import os
import shutil
import tempfile
import time

import psutil
import pythoncom
import win32com.client
import win32process

from engine.app_actions.base import AppActionContextChanged
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.decision import DecisionEngine


def excel_pids():
    found = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                found.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def main():
    before_pids = excel_pids()
    temp_dir = tempfile.mkdtemp(prefix="jarvis-phase3-excel-")
    workbook_path = os.path.join(temp_dir, "phase3_write_cell.xlsx")
    application = None
    workbook = None
    report = {
        "status": "failed",
        "blank_write_verified": False,
        "existing_value_waited_for_confirmation": False,
        "stale_fingerprint_blocked": False,
        "formula_verified": False,
        "default_active_object_connection_verified": False,
        "reopen_verified": False,
    }
    owned_pid = None
    pythoncom.CoInitialize()
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        _, owned_pid = win32process.GetWindowThreadProcessId(int(application.Hwnd))
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets(1)
        sheet.Name = "Stage3"
        sheet.Range("A1").Value2 = "기존"
        workbook.SaveAs(workbook_path, FileFormat=51)

        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
        )
        decision_engine = DecisionEngine()

        blank = adapter.prepare(
            "write_cell", {"cell": "B2", "value": "123", "value_type": "auto"}
        )
        assert decision_engine.evaluate(blank).decision == "execute"
        blank_result = adapter.execute(blank)
        report["blank_write_verified"] = (
            blank_result["verified"] and sheet.Range("B2").Value2 == 123
        )

        existing = adapter.prepare(
            "write_cell", {"cell": "A1", "value": "승인 후 값"}
        )
        existing_decision = decision_engine.evaluate(existing)
        report["existing_value_waited_for_confirmation"] = (
            existing_decision.requires_confirmation
            and sheet.Range("A1").Value2 == "기존"
        )

        sheet.Range("A1").Value2 = "사용자가 중간에 변경"
        try:
            adapter.execute(existing)
        except AppActionContextChanged:
            report["stale_fingerprint_blocked"] = (
                sheet.Range("A1").Value2 == "사용자가 중간에 변경"
            )

        refreshed = adapter.prepare(
            "write_cell", {"cell": "A1", "value": "승인 후 값"}
        )
        assert decision_engine.evaluate(
            refreshed, force_confirmation=True
        ).requires_confirmation
        adapter.execute(refreshed)

        formula = adapter.prepare(
            "write_cell", {"cell": "C1", "value": "=SUM(B2,7)"}
        )
        formula_result = adapter.execute(formula)
        report["formula_verified"] = (
            formula_result["verified"]
            and str(sheet.Range("C1").Formula).upper() == "=SUM(B2,7)"
            and sheet.Range("C1").Value2 == 130
        )

        workbook.Save()
        workbook.Close(SaveChanges=True)
        workbook = application.Workbooks.Open(workbook_path, ReadOnly=True)
        reopened = workbook.Worksheets("Stage3")
        report["reopen_verified"] = (
            reopened.Range("A1").Value2 == "승인 후 값"
            and reopened.Range("B2").Value2 == 123
            and str(reopened.Range("C1").Formula).upper() == "=SUM(B2,7)"
        )
        workbook.Close(SaveChanges=False)
        workbook = application.Workbooks.Open(workbook_path, ReadOnly=False)

        # Exercise the production connection path last. Releasing the last ROT
        # proxy can disconnect older proxies in a hidden automation-only server.
        active_object_adapter = ExcelAdapter(process_counter=lambda: 1)
        active_prepared = active_object_adapter.prepare(
            "write_cell", {"cell": "D1", "value": "ROT 연결 확인"}
        )
        active_result = active_object_adapter.execute(active_prepared)
        report["default_active_object_connection_verified"] = (
            active_result["verified"]
            and workbook.Worksheets("Stage3").Range("D1").Value2
            == "ROT 연결 확인"
        )
        report["status"] = (
            "passed"
            if all(
                report[key]
                for key in report
                if key != "status"
            )
            else "failed"
        )
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
        workbook = None
        application = None
        gc.collect()
        pythoncom.CoUninitialize()
        if owned_pid and owned_pid not in before_pids and psutil.pid_exists(owned_pid):
            try:
                process = psutil.Process(owned_pid)
                process.terminate()
                try:
                    process.wait(timeout=3)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)

    deadline = time.time() + 5
    while time.time() < deadline and excel_pids() - before_pids:
        time.sleep(0.1)
    report["excel_processes_left"] = sorted(excel_pids() - before_pids)
    report["temp_directory_removed"] = not os.path.exists(temp_dir)
    if report["excel_processes_left"] or not report["temp_directory_removed"]:
        report["status"] = "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
