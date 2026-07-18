"""Exercise the date, volume, and shell-block regressions in frozen Jarvis."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


def _core_audio_readable():
    from engine.system_volume import get_system_volume_state

    percent, muted = get_system_volume_state()
    return 0 <= percent <= 100 and isinstance(muted, bool)


def main():
    output = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="jarvis-exe-command-probe-") as data_dir:
        os.environ["JARVIS_DATA_DIR"] = data_dir

        from engine.parser import CommandParser
        from engine.version import runtime_info

        parser = CommandParser()
        parser.dict_mgr.noun_dict["echo"] = (
            r"C:\Program Files\Git\usr\bin\echo.exe"
        )
        parser.dict_mgr.noun_revision += 1

        with mock.patch("engine.builtins.set_system_volume", return_value=30), \
             mock.patch("engine.builtins.set_system_muted", return_value=False), \
             mock.patch("engine.builtins.os.startfile") as startfile:
            date_result = parser.execute_command_result("오늘 날짜 알려줘")
            volume_result = parser.execute_command_result(
                "볼륨을 30%로 설정해줘"
            )
            unmute_result = parser.execute_command_result("음소거 해제해줘")
            shell_result = parser.execute_command_result(
                "명령 프롬프트에서 echo JARVIS_TEST 실행해줘"
            )
            echo_result = parser.execute_command_result("echo 실행해줘")

        identity = runtime_info()
        results = {
            "frozen_runtime": identity.get("frozen") is True,
            "identity_matches_version": (
                identity.get("identity_matches_app_version") is True
            ),
            "core_audio_endpoint_readable": _core_audio_readable(),
            "date_is_local": (
                date_result.get("success") is True
                and date_result.get("action") == "date"
                and "Windows 메시지 박스" not in date_result.get("message", "")
            ),
            "volume_percentage_is_local": (
                volume_result.get("success") is True
                and volume_result.get("action") == "volume_set"
                and volume_result.get("target") == 30
            ),
            "unmute_is_local": (
                unmute_result.get("success") is True
                and unmute_result.get("action") == "unmute"
            ),
            "shell_request_blocked": (
                shell_result.get("status") == "blocked"
                and shell_result.get("action") == "command_line"
            ),
            "echo_open_bypass_blocked": (
                echo_result.get("status") == "blocked"
                and echo_result.get("action") == "open_app"
            ),
            "no_external_app_started": startfile.call_count == 0,
        }
        results["all_passed"] = all(results.values())
        results["runtime_info"] = identity
        results["messages"] = {
            "date": date_result.get("message"),
            "volume": volume_result.get("message"),
            "unmute": unmute_result.get("message"),
            "shell": shell_result.get("message"),
            "echo": echo_result.get("message"),
        }

    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not results["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
