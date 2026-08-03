"""Verify the frozen selection overlay against an already-open Excel window."""

from __future__ import annotations

import gc
import json
import os
import sys
import time
from pathlib import Path


def main():
    output = Path(sys.argv[1]).resolve()

    import pythoncom
    import win32com.client
    import win32con
    import win32gui
    import win32process

    from engine.edit_mode.selection_overlay import SelectionOverlayManager
    from engine.version import runtime_info

    pythoncom.CoInitialize()
    manager = SelectionOverlayManager()
    owned_excel = None
    owned_workbook = None
    owned_excel_pid = 0
    try:
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            owned_excel = win32com.client.DispatchEx("Excel.Application")
            owned_excel.Visible = True
            owned_excel.DisplayAlerts = False
            owned_workbook = owned_excel.Workbooks.Add()
            owned_workbook.Worksheets(1).Range("B14:D21").Select()
            owned_excel_pid = int(
                win32process.GetWindowThreadProcessId(int(owned_excel.Hwnd))[1]
            )
            excel = owned_excel
            time.sleep(0.5)
        selection = str(excel.Selection.Address).replace("$", "")
        owner_handle = int(excel.Hwnd)
        foreground_before = win32gui.GetForegroundWindow()
        status = manager.update(
            {"app_type": "excel", "window_handle": owner_handle},
            {"selection_kind": "range", "selection_reference": selection},
        )
        time.sleep(0.8)
        foreground_after = win32gui.GetForegroundWindow()

        windows = []

        def collect(handle, _):
            try:
                class_name = win32gui.GetClassName(handle)
                if not class_name.startswith(f"JarvisSelectionOverlay_{os.getpid()}_"):
                    return
                exstyle = win32gui.GetWindowLong(handle, win32con.GWL_EXSTYLE)
                windows.append({
                    "visible": bool(win32gui.IsWindowVisible(handle)),
                    "rectangle": list(win32gui.GetWindowRect(handle)),
                    "owner_handle": win32gui.GetWindow(handle, win32con.GW_OWNER),
                    "no_activate": bool(exstyle & win32con.WS_EX_NOACTIVATE),
                    "click_through": bool(exstyle & win32con.WS_EX_TRANSPARENT),
                    "layered": bool(exstyle & win32con.WS_EX_LAYERED),
                })
            except Exception:
                return

        win32gui.EnumWindows(collect, None)
        valid_window = any(
            item["visible"]
            and item["owner_handle"] == owner_handle
            and item["no_activate"]
            and item["click_through"]
            and item["layered"]
            for item in windows
        )
        results = {
            "frozen_runtime": runtime_info().get("frozen") is True,
            "selection_reference": selection,
            "overlay_status": status,
            "overlay_window_contract": valid_window,
            "foreground_unchanged": foreground_before == foreground_after,
            "windows": windows,
            "runtime_info": runtime_info(),
        }
        results["all_passed"] = all((
            results["frozen_runtime"],
            status.get("status") == "shown",
            valid_window,
            results["foreground_unchanged"],
        ))
        output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not results["all_passed"]:
            raise SystemExit(1)
    finally:
        manager.hide("probe_complete")
        manager.backend.close()
        if owned_workbook is not None:
            try:
                owned_workbook.Close(False)
            except Exception:
                pass
        if owned_excel is not None:
            try:
                owned_excel.Quit()
            except Exception:
                pass
        excel = None
        owned_workbook = None
        owned_excel = None
        gc.collect()
        if owned_excel_pid:
            try:
                import psutil

                process = psutil.Process(owned_excel_pid)
                try:
                    process.wait(timeout=2)
                except psutil.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        process.kill()
            except psutil.NoSuchProcess:
                pass
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
