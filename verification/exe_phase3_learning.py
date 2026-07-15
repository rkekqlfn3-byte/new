"""Create or reload one learned action using an isolated frozen data folder."""

import json
import sys
from pathlib import Path

from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


APP_NAME = "시스템"
MACRO_NAME = "3차_패키지_학습"
UTTERANCE = "3차 패키지 학습 실행"


def candidate():
    return {
        "app": APP_NAME,
        "name": MACRO_NAME,
        "desc": "패키지 학습 저장과 재실행 검증",
        "target": "",
        "code": "",
        "plan": [{"action": "wait", "seconds": 0.01}],
        "steps": [{"step": "짧게 기다린 뒤 완료"}],
        "utterances": [UTTERANCE],
        "learning": {
            "intent": "PACKAGE_PERSISTENCE_TEST",
            "argument_mode": "text",
            "verbs": ["실행"],
            "nouns": [],
            "utterances": [UTTERANCE],
            "slots": [],
        },
        "verification_status": "verified",
        "verification": [{"step": 1, "status": "verified"}],
    }


def main():
    output_path = Path(sys.argv[1]).resolve()
    parser = CommandParser()
    exists = MACRO_NAME in parser.dict_mgr.learned_macros.get(APP_NAME, {})
    mode = "reloaded"
    learning_message = ""
    if not exists:
        mode = "created"
        parser.pending_macros = [candidate()]
        learning_message = parser.approve_pending_learning()

    session_id = "exe-phase3-learning"
    result = parser.execute_command_result(UTTERANCE, session_id=session_id)
    if result.get("status") == "confirmation_required":
        confirmation = (result.get("data") or {}).get("confirmation") or {}
        result = parser.resolve_pending_confirmation(
            session_id,
            confirmation.get("confirmation_id"),
            "run_once",
        )
    reloaded = DictionaryManager()
    record = reloaded.learned_macros[APP_NAME][MACRO_NAME]
    report = {
        "mode": mode,
        "learning_message": learning_message,
        "execution_success": result["success"],
        "execution_status": result["status"],
        "execution_action": result["action"],
        "usage_count": record["usage_count"],
        "success_count": record["success_count"],
        "state": record["state"],
        "persisted": MACRO_NAME in reloaded.macro_dict,
    }
    report["all_passed"] = (
        report["execution_success"]
        and report["state"] == "active"
        and report["persisted"]
        and report["usage_count"] == report["success_count"]
    )
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
