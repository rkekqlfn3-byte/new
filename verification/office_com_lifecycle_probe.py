"""Live Excel lifecycle probe using only dedicated invisible instances."""

from __future__ import annotations

import gc
import json
import time

import psutil
import pythoncom
import win32com.client
import win32process

from engine.app_actions.excel_adapter import (
    ExcelAdapter,
    create_owned_excel_application,
)


def _excel_pids():
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                result.add(int(process.info["pid"]))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return result


def _application_pid(application):
    return int(win32process.GetWindowThreadProcessId(int(application.Hwnd))[1])


def _wait_for_pid_exit(pid, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.1)
    return not psutil.pid_exists(pid)


def _terminate_probe_pid(pid):
    if not pid or not psutil.pid_exists(pid):
        return
    try:
        process = psutil.Process(pid)
        if str(process.name()).casefold() != "excel.exe":
            return
        process.terminate()
        process.wait(timeout=3)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.TimeoutExpired):
        try:
            psutil.Process(pid).kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass


def _create_user_simulation():
    pythoncom.CoInitialize()
    application = workbook = None
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Add()
        pid = _application_pid(application)
        name = str(workbook.Name)
        return pid, name
    finally:
        workbook = None
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _close_user_simulation():
    pythoncom.CoInitialize()
    application = workbook = None
    try:
        application = win32com.client.GetActiveObject("Excel.Application")
        workbook = application.ActiveWorkbook
        workbook.Close(SaveChanges=False)
        workbook = None
        application.Quit()
    finally:
        workbook = None
        application = None
        gc.collect()
        pythoncom.CoUninitialize()


def _owned_getter(state):
    lease = create_owned_excel_application()
    application = lease.application
    try:
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Add()
        lease.register_owned_document(workbook)
        state["pid"] = _application_pid(application)
        return lease
    except Exception:
        lease.cleanup()
        raise


def main():
    baseline = _excel_pids()
    report = {"baseline_excel_pids": sorted(baseline)}
    if baseline:
        report.update({
            "passed": False,
            "skipped": "Existing Excel processes detected; live probe refused to attach.",
        })
        print(json.dumps(report, ensure_ascii=False))
        return 2

    probe_pids = set()
    user_pid = None
    try:
        user_pid, workbook_name = _create_user_simulation()
        probe_pids.add(user_pid)
        adapter = ExcelAdapter(
            process_counter=lambda: 1,
            com_runtime=pythoncom,
            discovery_retry_delay=0.05,
        )
        for row in range(1, 101):
            prepared = adapter.prepare(
                "write_cell", {"cell": f"A{row}", "value": row}
            )
            result = adapter.execute(prepared)
            if not result.get("verified"):
                raise RuntimeError(f"Excel write verification failed at row {row}")
        report["attached_100_commands_verified"] = True
        report["attached_single_pid"] = _excel_pids() == {user_pid}
        report["attached_application_preserved"] = psutil.pid_exists(user_pid)
        report["attached_workbook_name"] = workbook_name
        _close_user_simulation()
        report["attached_cleanup_after_probe"] = _wait_for_pid_exit(user_pid)
        probe_pids.discard(user_pid)

        owned_state = {}
        owned_adapter = ExcelAdapter(
            application_getter=lambda: _owned_getter(owned_state),
            process_counter=lambda: 1,
            com_runtime=pythoncom,
        )
        with owned_adapter._application():
            pass
        owned_pid = owned_state["pid"]
        probe_pids.add(owned_pid)
        report["owned_application_exited"] = _wait_for_pid_exit(owned_pid)
        if report["owned_application_exited"]:
            probe_pids.discard(owned_pid)

        exception_state = {}
        exception_adapter = ExcelAdapter(
            application_getter=lambda: _owned_getter(exception_state),
            process_counter=lambda: 1,
            com_runtime=pythoncom,
        )
        try:
            with exception_adapter._application():
                raise RuntimeError("intentional lifecycle probe failure")
        except RuntimeError:
            pass
        exception_pid = exception_state["pid"]
        probe_pids.add(exception_pid)
        report["owned_exception_application_exited"] = _wait_for_pid_exit(
            exception_pid
        )
        if report["owned_exception_application_exited"]:
            probe_pids.discard(exception_pid)
    finally:
        if user_pid and psutil.pid_exists(user_pid):
            try:
                _close_user_simulation()
            except Exception:
                pass
        for pid in tuple(probe_pids):
            _terminate_probe_pid(pid)

    report["final_excel_pids"] = sorted(_excel_pids())
    report["returned_to_baseline"] = not report["final_excel_pids"]
    report["passed"] = all(
        value is True
        for key, value in report.items()
        if key not in {
            "baseline_excel_pids",
            "final_excel_pids",
            "attached_workbook_name",
        }
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
