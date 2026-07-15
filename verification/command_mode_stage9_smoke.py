"""Read-only Windows UIA smoke test for command-mode stage 9."""

from __future__ import annotations

import json
import subprocess
import time

import psutil
from pywinauto import Desktop

from engine.ui_automation import WindowsUIAutomation


def notepad_pids():
    found = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (process.info.get("name") or "").casefold() == "notepad.exe":
                found.add(process.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def find_new_notepad_window(baseline, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new_pids = notepad_pids() - baseline
        for window in Desktop(backend="uia").windows():
            try:
                if window.is_visible() and window.process_id() in new_pids:
                    return window
            except Exception:
                continue
        time.sleep(0.2)
    return None


def explorer_smoke(adapter):
    try:
        from win32com.client import Dispatch

        browsers = list(Dispatch("Shell.Application").Windows())
    except Exception as error:
        return {"status": "not_available", "reason": str(error)}
    for browser in browsers:
        try:
            path = str(browser.Document.Folder.Self.Path or "")
            window = Desktop(backend="uia").window(handle=int(browser.HWND))
            if not path or not window.is_visible():
                continue
            control, diagnostic = adapter._locate_control(
                window,
                {
                    "control_type": "Edit",
                    "name": "검색",
                    "match_mode": "contains",
                },
                editable=True,
            )
            return {
                "status": "passed",
                "path": path,
                "control": control.window_text(),
                "diagnostic": diagnostic,
            }
        except Exception:
            continue
    return {
        "status": "not_available",
        "reason": "열려 있는 파일 탐색기 창이 없습니다.",
    }


def main():
    baseline = notepad_pids()
    process = None
    window = None
    adapter = WindowsUIAutomation(
        {"메모장": "notepad.exe"}, search_timeout=3.0, max_search_depth=10
    )
    result = {"notepad": {}, "explorer": {}}
    try:
        process = subprocess.Popen(["notepad.exe"])
        window = find_new_notepad_window(baseline)
        if window is None:
            result["notepad"] = {
                "status": "failed",
                "reason": "JARVIS가 연 새 메모장 창을 찾지 못했습니다.",
            }
        else:
            last_error = None
            for control_type in ("Document", "Edit"):
                try:
                    control, diagnostic = adapter._locate_control(
                        window,
                        {
                            "control_type": control_type,
                            "name": "",
                            "match_mode": "auto",
                        },
                        editable=True,
                    )
                    result["notepad"] = {
                        "status": "passed",
                        "window": window.window_text(),
                        "control_type": control_type,
                        "control": control.window_text(),
                        "diagnostic": diagnostic,
                    }
                    break
                except Exception as error:
                    last_error = error
            if not result["notepad"]:
                result["notepad"] = {
                    "status": "failed",
                    "reason": str(last_error),
                }
        result["explorer"] = explorer_smoke(adapter)
    finally:
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
        for pid in notepad_pids() - baseline:
            try:
                psutil.Process(pid).terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if process is not None:
            try:
                process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                pass

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["notepad"].get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
