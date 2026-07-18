"""Record foreground-window transitions during a bounded manual/UI probe."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import win32gui


def main():
    output = Path(sys.argv[1]).resolve()
    expected = int(sys.argv[2])
    duration = max(0.1, min(float(sys.argv[3]), 10.0))
    expected_class = str(sys.argv[4]) if len(sys.argv) > 4 else ""
    started = time.perf_counter()
    transitions = []
    previous = None
    while time.perf_counter() - started < duration:
        handle = int(win32gui.GetForegroundWindow() or 0)
        if handle != previous:
            try:
                class_name = str(win32gui.GetClassName(handle) or "")
            except Exception:
                class_name = ""
            transitions.append({
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "handle": handle,
                "title": str(win32gui.GetWindowText(handle) or ""),
                "class_name": class_name,
            })
            previous = handle
        time.sleep(0.01)
    result = {
        "expected_handle": expected,
        "expected_class": expected_class,
        "expected_seen": any(
            item["handle"] == expected
            or (expected_class and item["class_name"] == expected_class)
            for item in transitions
        ),
        "transitions": transitions,
    }
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not result["expected_seen"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
