"""Repeat real Notepad and Explorer UI Automation against packaged Jarvis code.

The harness creates uniquely named temporary documents and closes only windows
whose handles it opened. Existing user windows are recorded and left untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import psutil
import win32gui
from pywinauto import Desktop

from engine.hotkeys import press_hotkey
from engine.ui_automation import UIAutomationError, WindowsUIAutomation


WM_CLOSE = 0x0010
DISCARD_LABELS = ("저장 안 함", "저장하지 않음", "don't save")


def wait_until(predicate, timeout=5.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def top_windows():
    result = []
    for window in Desktop(backend="uia").windows():
        try:
            if window.is_visible():
                result.append(window)
        except Exception:
            continue
    return result


def window_by_title(fragment, excluded_handles=()):
    wanted = str(fragment).casefold()
    excluded = set(excluded_handles)
    for window in top_windows():
        try:
            if window.handle not in excluded and wanted in window.window_text().casefold():
                return window
        except Exception:
            continue
    return None


def window_exists(handle):
    # Classic Notepad can keep its top-level HWND alive briefly after the
    # document window has disappeared. For a user-visible close operation,
    # an invisible HWND is already complete.
    return bool(
        handle
        and win32gui.IsWindow(int(handle))
        and win32gui.IsWindowVisible(int(handle))
    )


def discard_and_close_notepad(window, changed):
    handle = window.handle
    pid = window.process_id()
    win32gui.PostMessage(handle, WM_CLOSE, 0, 0)
    if not changed:
        if not wait_until(lambda: not window_exists(handle), timeout=4.0):
            raise RuntimeError("메모장 창이 닫히지 않았습니다.")
        return

    def find_dialog():
        for candidate in top_windows():
            try:
                if candidate.process_id() != pid:
                    continue
                buttons = candidate.descendants(control_type="Button")
                labels = [
                    str(button.window_text() or "").strip().casefold()
                    for button in buttons
                ]
                if any(
                    wanted in label
                    for label in labels
                    for wanted in DISCARD_LABELS
                ):
                    return candidate, buttons
            except Exception:
                continue
        return None

    dialog = wait_until(find_dialog, timeout=4.0)
    clicked_label = ""
    if dialog:
        clicked = False
        for button in dialog[1]:
            label = str(button.window_text() or "").strip().casefold()
            if any(wanted in label for wanted in DISCARD_LABELS):
                button.click_input()
                clicked = True
                clicked_label = label
                break
        if not clicked:
            raise RuntimeError(
                "저장하지 않기 버튼을 찾지 못했습니다: "
                + ", ".join(str(button.window_text()) for button in dialog[1])
            )
    if not wait_until(lambda: not window_exists(handle), timeout=4.0):
        remaining = []
        for candidate in top_windows():
            try:
                if candidate.process_id() == pid:
                    remaining.append({
                        "title": candidate.window_text(),
                        "handle": candidate.handle,
                        "class": candidate.class_name(),
                        "enabled": candidate.is_enabled(),
                    })
            except Exception:
                continue
        raise RuntimeError(
            "저장 취소 후 메모장 창이 닫히지 않았습니다. "
            f"clicked={clicked_label!r}, remaining={remaining!r}"
        )


def force_close_test_notepad(pid, protected_pids):
    if not pid or pid in protected_pids:
        return
    try:
        process = psutil.Process(pid)
        if process.name().casefold() == "notepad.exe":
            process.terminate()
            process.wait(timeout=2)
    except (psutil.Error, OSError):
        pass


def error_category(error):
    message = str(error)
    if "창을 찾지" in message:
        return "window_not_found"
    if "입력창" in message or "컨트롤" in message:
        return "control_not_found"
    if "같지" in message or "검증" in message:
        return "verification_mismatch"
    if "닫히지" in message or "버튼" in message:
        return "close_failed"
    if isinstance(error, UIAutomationError):
        return "uia_error"
    return "unexpected_error"


def run_notepad(root, iterations, baseline_handles, baseline_pids):
    cases = []
    adapter = WindowsUIAutomation({})
    decoy_path = root / "계속 열어둘 기준 창.txt"
    decoy_path.write_text("기준 창", encoding="utf-8")
    subprocess.Popen(["notepad.exe", str(decoy_path)])
    decoy = wait_until(lambda: window_by_title(decoy_path.name, baseline_handles), timeout=6.0)
    if decoy is None:
        raise RuntimeError("다중 창 검증용 메모장을 열지 못했습니다.")

    try:
        for index in range(iterations):
            started = time.monotonic()
            name = f"자비스 3차 반복 {index + 1:02d} 공백.txt"
            path = root / name
            original = f"원본-{index + 1}"
            path.write_text(original, encoding="utf-8")
            pid = None
            window = None
            try:
                process = subprocess.Popen(["notepad.exe", str(path)])
                pid = process.pid
                adapter.noun_dict[name] = "notepad"
                window = wait_until(
                    lambda: window_by_title(name, baseline_handles | {decoy.handle}),
                    timeout=6.0,
                )
                if window is None:
                    raise UIAutomationError(f"실행 중인 UI Automation 창을 찾지 못했습니다: {name}")
                pid = window.process_id()
                if index % 5 == 4:
                    time.sleep(0.35)
                first = f"자비스 메모장 입력 {index + 1}회"
                second = f"자비스 전체 선택 후 교체 {index + 1}회"
                first_result = adapter.set_text(name, "", first)
                second_result = adapter.set_text(name, "", second)
                if first_result.get("status") != "verified":
                    raise RuntimeError("첫 텍스트 입력 검증이 일치하지 않습니다.")
                if second_result.get("status") != "verified" or second_result.get("value") != second:
                    raise RuntimeError("교체 텍스트 검증이 일치하지 않습니다.")
                discard_and_close_notepad(window, changed=True)
                if path.read_text(encoding="utf-8") != original:
                    raise RuntimeError("저장하지 않고 닫았지만 원본 파일이 변경됐습니다.")
                cases.append({
                    "index": index + 1,
                    "success": True,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    "delayed_case": index % 5 == 4,
                    "multiple_windows": True,
                })
            except Exception as error:
                cases.append({
                    "index": index + 1,
                    "success": False,
                    "category": error_category(error),
                    "error": str(error),
                    "traceback": traceback.format_exc(limit=4),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })
                if window and window_exists(window.handle):
                    try:
                        discard_and_close_notepad(window, changed=True)
                    except Exception:
                        force_close_test_notepad(pid, baseline_pids | {decoy.process_id()})
                else:
                    force_close_test_notepad(pid, baseline_pids | {decoy.process_id()})
    finally:
        if window_exists(decoy.handle):
            discard_and_close_notepad(decoy, changed=False)

    successes = sum(1 for case in cases if case["success"])
    categories = {}
    for case in cases:
        if not case["success"]:
            categories[case["category"]] = categories.get(case["category"], 0) + 1
    return {
        "attempts": len(cases),
        "successes": successes,
        "success_rate": round(successes * 100 / len(cases), 2) if cases else 0,
        "failure_categories": categories,
        "delayed_cases": sum(1 for case in cases if case.get("delayed_case")),
        "multiple_window_cases": sum(1 for case in cases if case.get("multiple_windows")),
        "cases": cases,
    }


def explorer_views():
    result = {}
    try:
        from win32com.client import Dispatch

        for browser in Dispatch("Shell.Application").Windows():
            try:
                result[int(browser.HWND)] = str(browser.Document.Folder.Self.Path)
            except Exception:
                continue
    except Exception:
        pass
    return result


def explorer_view_at(adapter, directory, excluded_handles=()):
    excluded = set(excluded_handles)
    views = adapter._explorer_views_at(directory)
    return next((item for item in views if item[0].handle not in excluded), None)


def close_temp_explorer_windows(root, protected_handles):
    root_key = os.path.normcase(os.path.abspath(str(root)))
    for handle, path in explorer_views().items():
        current = os.path.normcase(os.path.abspath(str(path)))
        if handle not in protected_handles and (current == root_key or current.startswith(root_key + os.sep)):
            win32gui.PostMessage(handle, WM_CLOSE, 0, 0)


def wait_for_opened_text_file(filename, excluded_handles):
    return wait_until(lambda: window_by_title(filename, excluded_handles), timeout=5.0)


def run_explorer(root, iterations, baseline_explorer, baseline_notepad_handles):
    adapter = WindowsUIAutomation({})
    folder_a = root / "한글 폴더 A"
    folder_b = root / "공백 있는 폴더 B"
    large_folder = root / "큰 폴더 500개"
    for folder in (folder_a, folder_b, large_folder):
        folder.mkdir()
    same_name = "같은 이름 문서.txt"
    (folder_a / same_name).write_text("A", encoding="utf-8")
    (folder_b / same_name).write_text("B", encoding="utf-8")
    for index in range(500):
        (large_folder / f"항목 {index:03d}.txt").write_text(str(index), encoding="utf-8")
    large_target = large_folder / "항목 499.txt"

    subprocess.Popen(["explorer.exe", str(root)])
    root_view = wait_until(
        lambda: explorer_view_at(adapter, root, baseline_explorer), timeout=8.0
    )
    if root_view is None:
        raise RuntimeError("검증용 파일 탐색기 창을 열지 못했습니다.")
    test_handle = root_view[0].handle
    cases = []
    try:
        targets = [folder_a / same_name, folder_b / same_name, large_target]
        for index in range(iterations):
            started = time.monotonic()
            target = targets[index % len(targets)]
            try:
                current = explorer_view_at(adapter, root, baseline_explorer)
                if current is None:
                    current = next(
                        (item for item in adapter._explorer_views_at(target.parent)
                         if item[0].handle == test_handle),
                        None,
                    )
                if current is None:
                    raise UIAutomationError("검증용 탐색기 창을 찾지 못했습니다.")
                current[1].Navigate(str(target.parent))
                target_view = wait_until(
                    lambda: next(
                        (item for item in adapter._explorer_views_at(target.parent)
                         if item[0].handle == test_handle),
                        None,
                    ),
                    timeout=6.0,
                )
                if target_view is None:
                    raise UIAutomationError("지정 폴더 이동 결과를 확인하지 못했습니다.")
                runtime = adapter.select_explorer_file(str(target))
                if runtime.get("status") != "verified":
                    raise RuntimeError("파일 선택 결과 검증이 일치하지 않습니다.")
                selected = [
                    os.path.normcase(os.path.abspath(str(item.Path)))
                    for item in target_view[1].Document.SelectedItems()
                ]
                if os.path.normcase(os.path.abspath(str(target))) not in selected:
                    raise RuntimeError("같은 이름 파일의 실제 선택 경로가 일치하지 않습니다.")

                target_view[0].set_focus()
                press_hotkey(["enter"])
                opened = wait_for_opened_text_file(target.name, baseline_notepad_handles)
                if opened is None:
                    raise RuntimeError("선택 파일이 열리지 않았습니다.")
                discard_and_close_notepad(opened, changed=False)

                target_view[0].set_focus()
                press_hotkey(["alt", "left"])
                moved_back = wait_until(
                    lambda: explorer_views().get(test_handle) not in {None, str(target.parent)},
                    timeout=5.0,
                )
                if not moved_back:
                    raise RuntimeError("탐색기 뒤로 이동을 확인하지 못했습니다.")
                cases.append({
                    "index": index + 1,
                    "success": True,
                    "target_kind": "large_folder" if target == large_target else "same_name",
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })
            except Exception as error:
                cases.append({
                    "index": index + 1,
                    "success": False,
                    "category": error_category(error),
                    "error": str(error),
                    "traceback": traceback.format_exc(limit=4),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })

        missing_rejected = False
        before = dict(explorer_views())
        try:
            adapter.select_explorer_file(str(folder_a / "존재하지 않음.txt"))
        except UIAutomationError:
            missing_rejected = explorer_views() == before
    finally:
        close_temp_explorer_windows(root, baseline_explorer)
        wait_until(lambda: not window_exists(test_handle), timeout=4.0)

    baseline_after = explorer_views()
    baseline_preserved = all(
        baseline_after.get(handle) == path for handle, path in baseline_explorer.items()
    )
    successes = sum(1 for case in cases if case["success"])
    categories = {}
    for case in cases:
        if not case["success"]:
            categories[case["category"]] = categories.get(case["category"], 0) + 1
    return {
        "attempts": len(cases),
        "successes": successes,
        "success_rate": round(successes * 100 / len(cases), 2) if cases else 0,
        "failure_categories": categories,
        "missing_file_rejected_without_navigation": missing_rejected,
        "preexisting_explorer_preserved": baseline_preserved,
        "cases": cases,
    }


def main():
    output_path = Path(sys.argv[1]).resolve()
    iterations = max(1, int(os.environ.get("JARVIS_PHASE3_ITERATIONS", "20")))
    initial_windows = top_windows()
    baseline_notepad = {
        window.handle
        for window in initial_windows
        if "메모장" in str(window.window_text()) or "notepad" in str(window.window_text()).casefold()
    }
    baseline_notepad_pids = {
        window.process_id() for window in initial_windows if window.handle in baseline_notepad
    }
    baseline_explorer = explorer_views()
    with tempfile.TemporaryDirectory(prefix="jarvis-phase3-windows-") as temp_dir:
        root = Path(temp_dir)
        notepad_root = root / "notepad"
        notepad_root.mkdir()
        notepad = run_notepad(
            notepad_root,
            iterations,
            baseline_notepad,
            baseline_notepad_pids,
        )
        explorer_root = root / "explorer"
        explorer_root.mkdir()
        explorer = run_explorer(
            explorer_root,
            iterations,
            baseline_explorer,
            baseline_notepad,
        )
    report = {
        "iterations": iterations,
        "notepad": notepad,
        "explorer": explorer,
        "thresholds": {
            "notepad_minimum": 90,
            "explorer_minimum": 85,
        },
    }
    report["all_passed"] = (
        notepad["success_rate"] >= 90
        and explorer["success_rate"] >= 85
        and explorer["missing_file_rejected_without_navigation"]
        and explorer["preexisting_explorer_preserved"]
    )
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
