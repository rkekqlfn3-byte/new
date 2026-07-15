"""Verify clean first-run data and preservation with the packaged executable."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


CORE_FILES = ("dictionaries.json", "user_memory.json", "user_preferences.json")


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _snapshot(data_dir: Path):
    result = {}
    if not data_dir.is_dir():
        return result
    for path in sorted(data_dir.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(data_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _run_hidden(executable: Path, data_dir: Path, seconds=4):
    env = os.environ.copy()
    env["JARVIS_DATA_DIR"] = str(data_dir)
    env["JARVIS_BROWSER_MODE"] = "none"
    env["JARVIS_PORT"] = str(_free_port())
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [str(executable)], env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=creation_flags,
    )
    time.sleep(seconds)
    alive = process.poll() is None
    if alive:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return alive, process.returncode


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def probe(executable: Path, existing_data_dir: Path):
    clean_root = Path(tempfile.mkdtemp(prefix="jarvis-phase3-clean-"))
    clean_data = clean_root / "data"
    try:
        clean_alive, clean_exit = _run_hidden(executable, clean_data)
        clean_files_exist = all((clean_data / name).is_file() for name in CORE_FILES)
        clean_dictionary = _load(clean_data / "dictionaries.json") if clean_files_exist else {}
        clean_memory = _load(clean_data / "user_memory.json") if clean_files_exist else {}
        clean_preferences = _load(clean_data / "user_preferences.json") if clean_files_exist else {}
        clean_result = {
            "process_alive_after_startup": clean_alive,
            "return_code_after_termination": clean_exit,
            "core_files_created": clean_files_exist,
            "api_key_empty": not bool((clean_dictionary.get("ai_config") or {}).get("api_key")),
            "learned_macros_empty": not bool(clean_dictionary.get("learned_macros")),
            "memory_empty": not any(str(value or "").strip() for value in clean_memory.values()),
            "preferences_empty": not bool(clean_preferences.get("preferences")),
            "startup_error_log_absent": not (clean_root / "jarvis-startup.log").exists(),
        }

        before = _snapshot(existing_data_dir)
        existing_dictionary_before = _load(existing_data_dir / "dictionaries.json")
        existing_alive, existing_exit = _run_hidden(executable, existing_data_dir)
        after = _snapshot(existing_data_dir)
        existing_dictionary_after = _load(existing_data_dir / "dictionaries.json")
        before_key = str((existing_dictionary_before.get("ai_config") or {}).get("api_key") or "")
        after_key = str((existing_dictionary_after.get("ai_config") or {}).get("api_key") or "")
        existing_result = {
            "process_alive_after_startup": existing_alive,
            "return_code_after_termination": existing_exit,
            "all_files_byte_identical": before == after,
            "file_count_before": len(before),
            "file_count_after": len(after),
            "api_key_preserved": bool(before_key) and before_key == after_key,
            "noun_count_before": len(existing_dictionary_before.get("noun_dictionary", {})),
            "noun_count_after": len(existing_dictionary_after.get("noun_dictionary", {})),
            "learned_app_count_before": len(existing_dictionary_before.get("learned_macros", {})),
            "learned_app_count_after": len(existing_dictionary_after.get("learned_macros", {})),
        }
        clean_passed = all(
            clean_result[key] for key in (
                "process_alive_after_startup", "core_files_created", "api_key_empty",
                "learned_macros_empty", "memory_empty", "preferences_empty",
                "startup_error_log_absent",
            )
        )
        existing_passed = all(
            existing_result[key] for key in (
                "process_alive_after_startup", "all_files_byte_identical",
                "api_key_preserved",
            )
        )
        return {
            "status": "passed" if clean_passed and existing_passed else "failed",
            "clean_first_run": clean_result,
            "existing_data_update": existing_result,
            "secret_values_printed": False,
        }
    finally:
        shutil.rmtree(clean_root, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    parser.add_argument("--existing-data-dir", type=Path, default=base / "Jarvis" / "data")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = probe(args.exe.resolve(), args.existing_data_dir.resolve())
    except (OSError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        result = {
            "status": "failed", "error_type": type(error).__name__,
            "secret_values_printed": False,
        }
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"phase3_user_data_probe={result['status']}")
    if "clean_first_run" in result:
        for key, value in result["clean_first_run"].items():
            print(f"clean_{key}={value}")
        for key, value in result["existing_data_update"].items():
            print(f"existing_{key}={value}")
    else:
        print(f"error_type={result.get('error_type', 'unknown')}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
