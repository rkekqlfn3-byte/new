"""Minimal child-process entry point for dynamic macros in frozen builds."""

from __future__ import annotations

import os
import runpy
import sys
import traceback

MACRO_WORKER_FLAG = "--jarvis-macro-worker"


def is_macro_worker(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    return bool(args and args[0] == MACRO_WORKER_FLAG)


def run_macro_worker(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != MACRO_WORKER_FLAG:
        return None
    # Frozen windowed executables inherit Windows' legacy text encoding even
    # when the parent captures pipes as UTF-8. Make the worker protocol exact.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    if len(args) < 2:
        print("매크로 작업자에 실행할 Python 파일이 없습니다.", file=sys.stderr)
        return 2
    macro_path = os.path.abspath(args[1])
    argument = args[2] if len(args) > 2 else ""
    if not os.path.isfile(macro_path):
        print(f"매크로 파일을 찾을 수 없습니다: {macro_path}", file=sys.stderr)
        return 2
    original_argv = sys.argv
    try:
        sys.argv = [macro_path, argument]
        runpy.run_path(macro_path, run_name="__main__")
        return 0
    except SystemExit as error:
        code = error.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv
