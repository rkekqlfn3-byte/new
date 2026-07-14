"""Portable build-time module runner with pywin32 DLL bootstrapping."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


_DLL_HANDLES = []


def configure_pywin32_dlls():
    """Register pywin32_system32 when packages come from a copied venv."""
    if not hasattr(os, "add_dll_directory"):
        return []
    candidates = []
    explicit = os.environ.get("JARVIS_PYWIN32_DLL_DIR")
    if explicit:
        candidates.append(Path(explicit))
    for entry in sys.path:
        if entry:
            candidates.append(Path(entry) / "pywin32_system32")
    added = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        _DLL_HANDLES.append(os.add_dll_directory(str(resolved)))
        added.append(str(resolved))
    return added


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m verification.runtime_entrypoint MODULE [ARGS...]")
    configure_pywin32_dlls()
    module = sys.argv[1]
    sys.argv = [module, *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
