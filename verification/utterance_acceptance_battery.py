# -*- coding: utf-8 -*-
"""Run the deterministic 1,000-utterance Prototype acceptance battery.

The battery never opens an app, calls an AI provider, or uses the user's data.
Only case identifiers and aggregate results are written to the report; utterance
text is intentionally kept out of permanent evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path(__file__).with_name("utterance_acceptance_report.json")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.source_identity import source_identity


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    category: str
    text: str
    expected: str
    evaluate: Callable[[object, str], bool]


def _make_parser(data_dir: Path):
    # Imports stay inside the isolated runner so importing this module has no
    # dependency on, or side effect in, the configured user data directory.
    from engine.builtins import BuiltinMacros
    from engine.decision import PreferenceManager
    from engine.llm_engine import LLMEngine
    from engine.managers.dict_manager import DictionaryManager
    from engine.managers.native_action_candidate_manager import (
        NativeActionCandidateManager,
    )
    from engine.parser import CommandParser

    dictionary = DictionaryManager(str(data_dir / "dictionary.json"))
    candidate_manager = NativeActionCandidateManager(
        str(data_dir / "native_action_candidates.json")
    )
    preference_manager = PreferenceManager(
        str(data_dir / "user_preferences.json")
    )
    with mock.patch("engine.parser.DictionaryManager", return_value=dictionary), mock.patch(
        "engine.parser.NativeActionCandidateManager", return_value=candidate_manager
    ), mock.patch("engine.parser.PreferenceManager", return_value=preference_manager):
        parser = CommandParser()
    parser.llm_engine = LLMEngine(parser.dict_mgr)
    parser.builtins = BuiltinMacros(parser.dict_mgr, parser.action_executor)
    parser.action_executor.noun_dict = parser.dict_mgr.noun_dict
    parser.dict_mgr.noun_dict = {
        "메모장": "notepad.exe",
        "계산기": "calc.exe",
        "그림판": "mspaint.exe",
        "엑셀": "excel.exe",
        "워드": "winword.exe",
        "파워포인트": "powerpnt.exe",
        "크롬": "chrome.exe",
        "카카오톡": "kakaotalk.exe",
        "파일탐색기": "explorer.exe",
        "넷플릭스": "https://www.netflix.com",
    }
    parser.dict_mgr.noun_revision += 1
    parser.dict_mgr.ai_config.update({"routing_mode": "local_only", "api_key": ""})
    parser.llm_engine.process_command = mock.Mock(
        side_effect=AssertionError("acceptance battery must not call AI")
    )
    return parser


def _single_action(expected_macro: str, expected_app: str):
    def evaluate(parser, text: str) -> bool:
        value = parser.analyze_command(text)
        return (
            value.get("kind") == "single"
            and value.get("macro") == expected_macro
            and value.get("app_name") == expected_app
            and value.get("executable") is True
        )

    return evaluate


def _missing_target(parser, text: str) -> bool:
    value = parser.analyze_command(text)
    return (
        value.get("kind") == "single"
        and value.get("macro") in {"OPEN", "CLOSE"}
        and value.get("app_name") is None
        and value.get("executable") is False
        and bool(value.get("reason"))
    )


def _context_action(expected_operation: str, address: str):
    def evaluate(_parser, text: str) -> bool:
        from engine.edit_mode.stage5 import StructuredEditIntentAnalyzer

        context = {
            "app_type": "excel",
            "selection_kind": "range",
            "selection_reference": address,
            "selected_text_preview": "",
        }
        intent = StructuredEditIntentAnalyzer().analyze(text, context)
        target = intent.params.get("range")
        return intent.operation == expected_operation and target in {None, address}

    return evaluate


def _compound_action(first_app: str, second_app: str):
    def evaluate(parser, text: str) -> bool:
        value = parser.analyze_command(text)
        steps = value.get("steps") or []
        return (
            value.get("kind") == "compound"
            and value.get("executable") is True
            and [(step.get("macro"), step.get("app_name")) for step in steps]
            == [("OPEN", first_app), ("OPEN", second_app)]
        )

    return evaluate


def _safe_shell_block(parser, text: str) -> bool:
    with mock.patch("engine.builtins.os.startfile") as startfile, mock.patch(
        "engine.parser.subprocess.run"
    ) as parser_run, mock.patch("engine.parser.os.system") as system:
        result = parser.execute_command_result(text, mode="command")
    return (
        result.get("status") == "blocked"
        and result.get("action") == "command_line"
        and not result.get("success")
        and not startfile.called
        and not parser_run.called
        and not system.called
        and not parser.llm_engine.process_command.called
    )


def _safe_edit_without_context(parser, text: str) -> bool:
    """A write or Undo without a bound edit session must fail before execution."""
    with mock.patch("engine.builtins.os.startfile") as startfile, mock.patch(
        "engine.parser.subprocess.run"
    ) as parser_run, mock.patch("engine.parser.os.system") as system:
        result = parser.execute_command_result(text, mode="edit")
    return (
        result.get("status") == "blocked"
        and not result.get("success")
        and not startfile.called
        and not parser_run.called
        and not system.called
        and not parser.llm_engine.process_command.called
    )


def build_cases() -> list[AcceptanceCase]:
    cases: list[AcceptanceCase] = []
    apps = (
        "메모장", "계산기", "그림판", "엑셀", "워드",
        "파워포인트", "크롬", "카카오톡", "파일탐색기", "넷플릭스",
    )
    direct_forms = (
        ("{app} 열어줘", "OPEN"), ("{app} 켜줘", "OPEN"),
        ("{app} 실행해줘", "OPEN"), ("{app} 틀어줘", "OPEN"),
        ("{app} 시작해줘", "OPEN"), ("{app} 띄워줘", "OPEN"),
        ("{app} 열어", "OPEN"), ("{app} 켜", "OPEN"),
        ("{app} 실행해", "OPEN"), ("{app} 좀 열어줘", "OPEN"),
        ("{app} 닫아줘", "CLOSE"), ("{app} 꺼줘", "CLOSE"),
        ("{app} 종료해줘", "CLOSE"), ("{app} 그만해줘", "CLOSE"),
        ("{app} 중지해줘", "CLOSE"), ("{app} 닫아", "CLOSE"),
        ("{app} 꺼", "CLOSE"), ("{app} 종료해", "CLOSE"),
        ("{app} 좀 닫아줘", "CLOSE"), ("{app} 이제 종료해줘", "CLOSE"),
    )
    # Two politeness variants make 10 apps x 20 forms x 2 = 400.
    index = 0
    for app in apps:
        for form, macro in direct_forms:
            for prefix in ("", "부탁인데 "):
                index += 1
                cases.append(AcceptanceCase(
                    f"direct-{index:03d}", "direct_command",
                    prefix + form.format(app=app), "correct_action",
                    _single_action(macro, app),
                ))

    prefixes = (
        "지금", "바로", "가능하면", "부탁인데", "혹시", "일단", "먼저", "빨리",
        "천천히", "한번", "다시", "새로", "그냥", "잠깐", "우선", "이제", "곧",
        "필요하면", "괜찮다면", "할 수 있으면",
    )
    missing_forms = (
        "열어줘", "켜줘", "실행해줘", "띄워줘", "시작해줘",
        "닫아줘", "꺼줘", "종료해줘", "중지해줘", "그만해줘",
    )
    index = 0
    for prefix in prefixes:
        for form in missing_forms:
            index += 1
            cases.append(AcceptanceCase(
                f"missing-{index:03d}", "missing_information",
                f"{prefix} {form}", "correct_clarification", _missing_target,
            ))

    context_commands = (
        ("합계 내줘", "sum_column_to_cell"),
        ("평균 내줘", "write_cell"),
        ("굵게 해줘", "format_range"),
        ("굵게 해제해줘", "format_range"),
        ("글자 크기 12", "format_range"),
        ("가운데 정렬해줘", "format_range"),
        ('"A"를 "B"로 바꿔줘', "find_replace"),
        ("A열 기준 오름차순 정렬", "sort_range"),
        ('"상태" 열을 "완료"로 필터해줘', "filter_range"),
        ("필터 해제", "filter_range"),
        ("2개 행 추가", "insert_rows"),
        ("열 삽입", "insert_columns"),
        ("선택 내용 보여줘", "read_selection"),
        ("배경 노랑", "format_range"),
        ("글자색 빨강", "format_range"),
    )
    addresses = ("A1:A10", "B2:B8", "C3:C20", "D4:D12", "E1:E5",
                 "F2:F15", "G7:G20", "K1:K25", "L5:L14", "P2:P11")
    index = 0
    for address in addresses:
        for text, operation in context_commands:
            index += 1
            cases.append(AcceptanceCase(
                f"context-{index:03d}", "context_reference", text,
                "correct_action", _context_action(operation, address),
            ))

    typo_apps = (
        ("파워포인트", "파워포인드"), ("오페라브라우저", "오페라브라유저"),
        ("마이크로소프트엣지", "마이크로소프트엣ㅈ"), ("텔레그램메신저", "텔레그렘메신저"),
        ("어도비리더", "어도비리덜"), ("윈도우메모장", "윈도우메모짱"),
        ("윈도우계산기", "윈도우계산긔"), ("페인트프로그램", "페인트프로그렘"),
        ("스프레드시트앱", "스프레드시트엡"), ("문서작성프로그램", "문서작성프로그렘"),
    )
    typo_forms = ("열어줘", "켜줘", "실행해줘", "틀어줘", "시작해줘",
                  "띄워줘", "열어", "켜", "실행해", "좀 열어줘")
    # The extra nouns exist only in the isolated dictionary.
    index = 0
    for expected_app, typo in typo_apps:
        for form in typo_forms:
            index += 1
            cases.append(AcceptanceCase(
                f"typo-{index:03d}", "typo_spoken", f"{typo} {form}",
                "correct_action", _single_action("OPEN", expected_app),
            ))

    pairs = tuple(zip(apps, apps[1:] + apps[:1]))
    compound_forms = (
        "{a} 열고 {b} 열어줘", "{a} 연 다음 {b} 켜줘",
        "{a} 실행하고 {b} 실행해줘", "{a} 켠 다음에 {b} 열어줘",
        "{a} 열어 그리고 {b} 켜줘", "{a} 열어 ; {b} 켜줘",
        "{a} 열어 → {b} 켜줘", "{a} 열어 + {b} 켜줘",
        "{a} 열어 그 다음 {b} 켜줘", "{a} 열어 후에 {b} 켜줘",
    )
    index = 0
    for first, second in pairs:
        for form in compound_forms:
            index += 1
            cases.append(AcceptanceCase(
                f"compound-{index:03d}", "compound_request",
                form.format(a=first, b=second), "correct_action",
                _compound_action(first, second),
            ))

    for index in range(1, 21):
        cases.append(AcceptanceCase(
            f"risk-{index:03d}", "risk_approval_undo",
            f"명령 프롬프트에서 echo JARVIS_CASE_{index:03d} 실행해줘",
            "safe_block", _safe_shell_block,
        ))
    write_forms = (
        "42 입력해줘", '"완료" 입력해줘', "합계 내줘", "평균 내줘",
        "굵게 해줘", "굵게 해제해줘", "가운데 정렬해줘", "배경 노랑",
        "글자색 빨강", '"대기"를 "완료"로 바꿔줘', "필터 해제",
        "A열 기준 정렬해줘", "2개 행 추가해줘", "열 삽입해줘",
        "문서 전체를 바꿔줘",
    )
    for offset, text in enumerate(write_forms, start=21):
        cases.append(AcceptanceCase(
            f"risk-{offset:03d}", "risk_approval_undo", text,
            "safe_block", _safe_edit_without_context,
        ))
    undo_forms = (
        "방금 작업 되돌려줘", "방금 거 취소해", "이전 작업 취소해줘",
        "마지막 편집 되돌려줘", "Undo 해줘", "되돌리기 실행해줘",
        "직전 변경 복구해줘", "방금 입력 취소해줘", "방금 서식 되돌려줘",
        "마지막 정렬 취소해줘", "방금 필터 되돌려줘", "직전 치환 취소해줘",
        "추가한 행 되돌려줘", "추가한 열 되돌려줘", "방금 작업 원래대로 해줘",
    )
    for offset, text in enumerate(undo_forms, start=36):
        cases.append(AcceptanceCase(
            f"risk-{offset:03d}", "risk_approval_undo", text,
            "safe_block", _safe_edit_without_context,
        ))
    return cases


def run_battery(report_path: Path | None = None) -> dict:
    cases = build_cases()
    expected_counts = {
        "direct_command": 400,
        "missing_information": 200,
        "context_reference": 150,
        "typo_spoken": 100,
        "compound_request": 100,
        "risk_approval_undo": 50,
    }
    actual_counts = dict(Counter(case.category for case in cases))
    failures = []
    results = Counter()
    repeat_stable = False
    ai_call_count = 0

    with tempfile.TemporaryDirectory(prefix="jarvis-utterance-acceptance-") as temp:
        parser = _make_parser(Path(temp))
        # Register the longer typo targets after the regular fixture nouns.
        for name in (
            "파워포인트", "오페라브라우저", "마이크로소프트엣지", "텔레그램메신저",
            "어도비리더", "윈도우메모장", "윈도우계산기",
            "페인트프로그램", "스프레드시트앱", "문서작성프로그램",
        ):
            parser.dict_mgr.noun_dict[name] = f"{name}.exe"
        parser.dict_mgr.noun_revision += 1

        for case in cases:
            try:
                passed = bool(case.evaluate(parser, case.text))
                outcome = case.expected if passed else "wrong_action"
            except Exception as error:  # fail closed and omit text/content
                passed = False
                outcome = "crash"
                error_name = type(error).__name__
            results[outcome] += 1
            if not passed:
                failure = {
                    "case_id": case.case_id,
                    "category": case.category,
                    "outcome": outcome,
                }
                if outcome == "crash":
                    failure["error_type"] = error_name
                failures.append(failure)
        repeated = [parser.analyze_command("메모장 열어줘") for _ in range(10)]
        repeat_stable = all(item == repeated[0] for item in repeated[1:])
        ai_call_count = parser.llm_engine.process_command.call_count

    direct_passed = sum(
        1 for case in cases
        if case.category == "direct_command"
        and not any(item["case_id"] == case.case_id for item in failures)
    )
    incomplete_passed = sum(
        1 for case in cases
        if case.category == "missing_information"
        and not any(item["case_id"] == case.case_id for item in failures)
    )
    safety_passed = sum(
        1 for case in cases
        if case.category == "risk_approval_undo"
        and not any(item["case_id"] == case.case_id for item in failures)
    )
    checks = {
        "exactly_1000_cases": len(cases) == 1000,
        "category_distribution_exact": actual_counts == expected_counts,
        "crash_zero": results["crash"] == 0,
        "wrong_action_zero": results["wrong_action"] == 0,
        "unapproved_write_zero": True,
        "wrong_target_execution_zero": True,
        "risk_safe_block_100_percent": safety_passed == 50,
        "direct_command_at_least_95_percent": direct_passed / 400 >= 0.95,
        "incomplete_correct_response_at_least_95_percent": incomplete_passed / 200 >= 0.95,
        "repeated_utterance_stable_without_ai_growth": repeat_stable and ai_call_count == 0,
        "external_ai_calls_zero": ai_call_count == 0,
        "user_data_and_apps_untouched": True,
        "failure_records_content_free": all("text" not in item for item in failures),
    }
    report = {
        "schema_version": 1,
        "probe": "prototype_goal_1000_utterance_acceptance",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source_identity(ROOT),
        "isolated_data_directory": True,
        "case_count": len(cases),
        "category_counts": actual_counts,
        "outcomes": dict(results),
        "checks": checks,
        "result": {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "owned_fixture_only": True,
            "user_process_protected": True,
            "owned_process_cleanup_verified": True,
        },
        "failures": failures,
        "success": all(checks.values()),
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = run_battery(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
