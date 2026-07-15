"""Verify frozen dynamic macro output handling and verification semantics."""

import glob
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


_ISOLATED_DATA = tempfile.TemporaryDirectory(prefix="jarvis-exe-verified-data-")
os.environ["JARVIS_DATA_DIR"] = _ISOLATED_DATA.name

from engine.execution_result import success_result
from engine.macro_runner import MacroRunner
from engine.parser import CommandParser


def main():
    pattern = os.path.join(tempfile.gettempdir(), "jarvis-macro-*")
    before = set(glob.glob(pattern))
    result = MacroRunner(timeout=10).run(
        "import sys\n"
        "sys.stdout.write('A' * 1_000_000 + 'STDOUT_END')\n"
        "sys.stderr.write('B' * 1_000_000 + 'STDERR_END')\n"
    )
    parser = CommandParser()
    parser.dict_mgr.learned_macros = {"시스템": {"검증테스트": {
        "state": "active",
        "plan": [{"action": "type_text", "text": "테스트"}],
        "learning": {"utterances": ["검증 테스트 실행"]},
    }}}
    parser.dict_mgr.macro_dict["검증테스트"] = {
        "type": "learned", "app": "시스템",
        "synonyms": ["검증 테스트 실행"],
    }
    plan_result = success_result(
        "행동 계획 실행", action="action_plan", verified=False,
        verification_status="confirmation_required",
    )
    with (
        patch.object(
            parser.action_executor, "execute_plan", return_value=plan_result
        ),
        patch.object(parser.dict_mgr, "record_learned_macro_result"),
    ):
        waiting = parser.execute_command_result(
            "검증 테스트 실행", session_id="exe-verified-output"
        )
        learned_result = parser.resolve_pending_confirmation(
            "exe-verified-output",
            waiting["data"]["confirmation"]["confirmation_id"],
            "run_once",
        )
    leftovers = sorted(set(glob.glob(pattern)) - before)
    report = {
        "success": result["success"],
        "verified": result["verified"],
        "verification_status": result["verification_status"],
        "stdout_has_end": result["stdout"].endswith("STDOUT_END"),
        "stderr_has_end": result["stderr"].endswith("STDERR_END"),
        "stdout_length": len(result["stdout"]),
        "stderr_length": len(result["stderr"]),
        "learned_success": learned_result["success"],
        "learned_verified": learned_result["verified"],
        "learned_status": learned_result.get("status"),
        "learned_action": learned_result.get("action"),
        "learned_message": learned_result.get("message"),
        "learned_verification_status": learned_result.get(
            "verification_status", ""
        ),
        "temporary_leftovers": leftovers,
    }
    report["all_passed"] = (
        report["success"]
        and not report["verified"]
        and report["verification_status"] == "confirmation_required"
        and report["stdout_has_end"]
        and report["stderr_has_end"]
        and report["stdout_length"] <= 4000
        and report["stderr_length"] <= 4000
        and report["learned_success"]
        and not report["learned_verified"]
        and report["learned_verification_status"]
        == "manual_confirmation_required"
        and not leftovers
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if len(sys.argv) > 1 and sys.argv[1]:
        Path(sys.argv[1]).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
