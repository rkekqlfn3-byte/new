"""Verify that the packaged executable contains the dynamic-code policy."""

import json
import sys
from pathlib import Path

from engine.parser import CommandParser
from engine.security import BLOCKED, CONFIRMATION_REQUIRED, SAFE, DynamicCodePreflight


def main():
    output = Path(sys.argv[1]).resolve()
    preflight = DynamicCodePreflight()
    parser = CommandParser.__new__(CommandParser)
    parameterless_action = {
        "target": "현재 Excel 창 제목",
        "app_name": "Excel",
        "macro_name": "Excel 창 제목 표시",
        "description": "활성 Excel 제목을 표시",
        "code": (
            "import win32api\n"
            "import win32com.client\n"
            "excel = win32com.client.GetActiveObject('Excel.Application')\n"
            "win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)"
        ),
        "explanation_steps": [{
            "step": "Excel 창 제목 표시",
            "code_snippet": "win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)",
        }],
        "learning": {"argument_mode": "json", "slots": []},
    }
    results = {
        "safe_data_code": preflight.analyze(
            "import json, sys\nprint(json.loads(sys.argv[1]))"
        ).status == SAFE,
        "file_write_requires_confirmation": preflight.analyze(
            "open('C:/Temp/jarvis-probe.txt', 'w').write('probe')"
        ).status == CONFIRMATION_REQUIRED,
        "shell_is_blocked": preflight.analyze(
            "import subprocess\nsubprocess.run(['powershell.exe', '-c', 'dir'])"
        ).status == BLOCKED,
        "registry_is_blocked": preflight.analyze(
            "import winreg\nwinreg.SetValueEx(None, 'x', 0, 1, 'y')"
        ).status == BLOCKED,
        "excel_title_message_box_requires_confirmation": preflight.analyze(
            "import win32api\n"
            "import win32com.client\n"
            "excel = win32com.client.GetActiveObject('Excel.Application')\n"
            "win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)"
        ).status == CONFIRMATION_REQUIRED,
        "excluded_gui_modules_are_blocked": all(
            preflight.analyze(code).status == BLOCKED
            for code in ("import pygetwindow", "import tkinter", "import pyautogui")
        ),
        "parameterless_dynamic_validation_passes": (
            parser._validate_generated_code(
                parameterless_action, require_external_target=True
            ) is None
        ),
        "excel_foreground_lookup_is_rejected": "ActiveWindow.Caption" in (
            parser._validate_generated_code(
                {
                    **parameterless_action,
                    "code": (
                        "import win32gui\n"
                        "hwnd = win32gui.GetForegroundWindow()\n"
                        "print(win32gui.GetWindowText(hwnd))"
                    ),
                },
                require_external_target=True,
            ) or ""
        ),
    }
    results["all_passed"] = all(results.values())
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not results["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
