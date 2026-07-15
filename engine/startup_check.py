"""Preflight used by Jarvis_Start.bat before the windowed process launches."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


REQUIRED_MODULES = (
    "eel",
    "olefile",
    "psutil",
    "PyPDF2",
    "pythoncom",
    "win32api",
    "win32com.client",
    "win32con",
    "win32gui",
    "win32process",
    "pywinauto",
)
REQUIRED_PATHS = (
    Path("jarvis_app.py"),
    Path("gui") / "index.html",
    Path("default_data") / "dictionaries.json",
)


def main():
    errors = []
    if sys.version_info < (3, 10):
        errors.append("Python 3.10 이상이 필요합니다.")

    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as error:
            errors.append(f"모듈 {module_name}: {error}")

    for path in REQUIRED_PATHS:
        if not path.is_file():
            errors.append(f"필수 파일 없음: {path}")

    if errors:
        print("[ERROR] Jarvis 실행 환경 검증에 실패했습니다.")
        for error in errors:
            print(f"  - {error}")
        print("requirements.txt의 고정 버전을 설치한 뒤 다시 실행해주세요.")
        return 1

    print("[OK] Jarvis 실행 환경 검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
