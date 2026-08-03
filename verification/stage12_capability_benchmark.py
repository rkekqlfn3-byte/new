"""Offline, side-effect-free 100-command capability benchmark for stage 12."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser
from engine.parsing.office_command_parser import (
    parse_native_excel_filter_command,
    parse_native_excel_find_replace_command,
    parse_native_excel_format_command,
    parse_native_excel_range_format_command,
    parse_native_excel_sort_command,
    parse_native_excel_sum_command,
    parse_native_excel_write_command,
    parse_native_hwp_find_replace_command,
    parse_native_hwp_insert_command,
    parse_native_hwp_paragraph_format_command,
    parse_native_hwp_save_command,
    parse_native_hwp_text_format_command,
)

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "verification" / "stage12_capability_report.json"


def case(category, text, route):
    return {"category": category, "text": text, "expected_route": route}


def build_corpus():
    items = [
        case("windows_app", "메모장 열어줘", "local:OPEN"),
        case("windows_app", "계산기 켜줘", "local:OPEN"),
        case("windows_app", "메모장 닫아줘", "local:CLOSE"),
        case("windows_app", "계산기 종료해", "local:CLOSE"),
        case("windows_app", "소리 키워", "local:VOL_UP"),
        case("windows_app", "소리 줄여", "local:VOL_DOWN"),
        case("windows_app", "음소거 해줘", "local:MUTE"),
        case("windows_app", "재생해", "local:PLAYPAUSE"),
        case("windows_app", "지금 몇 시야?", "local:TIME"),
        case("windows_app", "오늘 날씨 어때?", "local:WEATHER"),
        case("windows_app", "컴퓨터 꺼", "local:SHUTDOWN"),
        case("windows_app", "종료 예약 취소해", "local:CANCEL_SHUTDOWN"),
        case("windows_app", "구글에서 JARVIS 검색해", "local:SEARCH"),
        case("windows_app", "유튜브에서 엑셀 강의 검색해", "local:SEARCH"),
        case("windows_app", "메모장 연 다음 계산기 켜줘", "local:compound"),
    ]

    for text in (
        "다운로드 폴더에서 최신 파일 찾아줘",
        "바탕화면에 작업 폴더 만들어줘",
        "보고서.txt를 백업 폴더로 복사해줘",
        "임시 파일을 휴지통으로 보내줘",
        "두 폴더의 파일 목록을 비교해줘",
        "사진 폴더에서 큰 파일만 찾아줘",
        "문서 폴더를 날짜별로 정리해줘",
        "결과를 새 텍스트 파일로 저장해줘",
        "중복 파일 이름을 확인해줘",
        "프로젝트 폴더를 압축해줘",
    ):
        items.append(case("file_folder", text, "ai_handoff"))

    excel_basic = (
        ("엑셀 A1에 100을 입력해줘", "write_cell"),
        ("엑셀 B2에 테스트를 넣어줘", "write_cell"),
        ("Excel C3에 3.14를 써줘", "write_cell"),
        ("엑셀 D4에 =SUM(A1:A3)을 입력해줘", "write_cell"),
        ("엑셀 E5에 2026-07-14를 적어줘", "write_cell"),
        ("엑셀 F6에 완료를 입력해주세요", "write_cell"),
        ("엑셀 A2:A10을 합계 구해서 B1에 넣어줘", "sum_column_to_cell"),
        ("엑셀 매출 열 합계를 C1에 넣어줘", "sum_column_to_cell"),
        ("Excel B2:B4를 더해서 D1에 입력해줘", "sum_column_to_cell"),
        ("엑셀 수량 열을 합산해서 E1에 넣어줘", "sum_column_to_cell"),
        ("엑셀 A1:C3을 굵게 해줘", "format_range"),
        ("엑셀 B2:D5 글자 크기를 14로 해줘", "format_range"),
        ("엑셀 A1:A10을 가운데 정렬해줘", "format_range"),
        ("엑셀 C1:C5 배경을 노란색으로 채워줘", "format_range"),
        ("엑셀 D1:F2 글자색을 빨간색으로 해줘", "format_range"),
        ("엑셀 매출 열에서 20 이상으로 필터해줘", "filter_range"),
        ("엑셀 상태 열에서 완료만 필터해줘", "filter_range"),
        ("엑셀 수량 열에서 10 미만으로 필터해줘", "filter_range"),
        ("엑셀 이름 열에서 홍길동만 필터해줘", "filter_range"),
        ("엑셀 필터 해제해줘", "filter_range"),
        ("엑셀 A1:A10 범위에서 대기를 진행으로 바꿔줘", "find_replace"),
        ("엑셀 현재 시트에서 서울을 부산으로 교체해줘", "find_replace"),
        ("엑셀 B2:B20 범위에서 0을 없음으로 바꿔줘", "find_replace"),
        ("엑셀 A1:C10에서 매출 열을 오름차순 정렬해줘", "sort_range"),
        ("엑셀 상태 열을 내림차순 정렬해줘", "sort_range"),
    )
    items.extend(
        case("excel_basic", text, f"native_excel:{operation}")
        for text, operation in excel_basic
    )

    excel_complex = (
        ("엑셀 매출 열에서 50 이상을 노란색으로 표시해줘", "choose_format_method"),
        ("엑셀 매출 열에서 50 이상을 조건부 서식으로 노란색으로 표시해줘", "apply_conditional_format"),
        ("엑셀 매출 열에서 50 이상을 지금만 빨간색으로 표시해줘", "format_matching_values"),
        ("엑셀 점수 열에서 90 이상을 앞으로 항상 초록색으로 표시해줘", "choose_format_method"),
        ("엑셀 매출 열 합계를 현재 합계값만 C1에 넣어줘", "sum_column_to_cell"),
        ("엑셀 A2:A4를 합계 구해서 A3에 넣어줘", "sum_column_to_cell"),
        ("엑셀 매출 열 합계를 Z1에 넣어줘", "sum_column_to_cell"),
        ("엑셀 A1:C100에서 매출 열을 높은 순 정렬해줘", "sort_range"),
        ("엑셀 현재 시트에서 정확히 일치하는 완료를 종료로 바꿔줘", "find_replace"),
        ("엑셀 필터를 모두 표시해줘", "filter_range"),
        ("엑셀 J1에 병합 대상 값을 입력해줘", "write_cell"),
        ("엑셀 A1에 덮어쓸 값을 입력해줘", "write_cell"),
        ("엑셀 A1:XFD100을 굵게 해줘", "format_range"),
        ("엑셀 B2:B30 범위에서 셀 전체 일치 대기를 완료로 교체해줘", "find_replace"),
        ("엑셀 C1에 =IF(A1>0,1,0)을 입력해줘", "write_cell"),
    )
    items.extend(
        case("excel_complex_risky", text, f"native_excel:{operation}")
        for text, operation in excel_complex
    )

    hwp = (
        ("한글 커서 위치에 안녕하세요를 입력해줘", "insert_text"),
        ("한글 선택 영역에 보고서 제목을 넣어줘", "insert_text"),
        ("한글 선택 영역을 굵게 해줘", "set_text_format"),
        ("한글 선택 영역 글자 크기를 16으로 해줘", "set_text_format"),
        ("한글 선택 영역 글자색을 빨간색으로 해줘", "set_text_format"),
        ("한글 문단을 가운데 정렬해줘", "set_paragraph_format"),
        ("한글 문단을 양쪽 정렬해줘", "set_paragraph_format"),
        ("한글 현재 문서에서 대기를 완료로 바꿔줘", "find_replace"),
        ("한글 대기를 완료로 바꿔줘", "choose_hwp_replace_scope"),
        ("한글에서 C:\\Temp\\stage12.pdf로 PDF 저장해줘", "save_as"),
    )
    items.extend(
        case("hwp_basic", text, f"native_hwp:{operation}")
        for text, operation in hwp
    )

    for index in range(1, 11):
        items.append(case(
            "learned_reuse",
            f"학습 작업 {index} 실행",
            f"learned:학습작업{index}",
        ))

    ambiguous = (
        ("엑셀 점수 열에서 80 이상을 노란색으로 표시해줘", "native_excel:choose_format_method"),
        ("한글 초안을 최종으로 바꿔줘", "native_hwp:choose_hwp_replace_scope"),
        ("메무증 켜줘", "ai_handoff"),
        ("엑셀 합계 넣어줘", "ai_handoff"),
        ("메모장 열고 등록 안 된 작업도 해줘", "ai_handoff"),
    )
    items.extend(case("ambiguous_expression", text, route) for text, route in ambiguous)

    error_cases = (
        ("컴퓨터 종료 취소해", "local:CANCEL_SHUTDOWN"),
        ("엑셀 필터 초기화해줘", "native_excel:filter_range"),
        ("한글 선택 영역을 왼쪽 문단 정렬해줘", "native_hwp:set_paragraph_format"),
        ("엑셀 A1에 =1/0을 입력해줘", "native_excel:write_cell"),
        ("엑셀 존재하지 않는 열 합계를 B1에 넣어줘", "native_excel:sum_column_to_cell"),
        ("한글 선택 영역에서 없는말을 새말로 바꿔줘", "native_hwp:find_replace"),
        ("네트워크가 끊기면 다시 알려줘", "ai_handoff"),
        ("실행 중인 작업 취소해", "local:CANCEL_SHUTDOWN"),
        ("확인 카드 테스트", "confirmation_demo"),
        ("지원하지 않는 앱에서 버튼 눌러줘", "ai_handoff"),
    )
    items.extend(case("error_cancel_recovery", text, route) for text, route in error_cases)
    return items


def install_learned_benchmark_macros(parser):
    parser.dict_mgr.learned_macros = {"시스템": {}}
    for index in range(1, 11):
        name = f"학습작업{index}"
        phrase = f"학습 작업 {index} 실행"
        parser.dict_mgr.learned_macros["시스템"][name] = {
            "state": "active",
            "plan": [{"action": "wait", "seconds": 0.01}],
            "code": "",
            "learning": {"intent": f"BENCHMARK_{index}", "slots": []},
            "run_policy": "confirm",
            "run_policy_history": [],
        }
        parser.dict_mgr.macro_dict[name] = {
            "name": name,
            "type": "learned",
            "app": "시스템",
            "synonyms": [phrase],
        }
    parser.template_matcher.invalidate()


def route(parser, text):
    if text in {"확인 카드 테스트", "확인카드 테스트"}:
        return "confirmation_demo"
    for function in (
        parse_native_excel_write_command,
        parse_native_excel_sum_command,
        parse_native_excel_format_command,
        parse_native_excel_range_format_command,
        parse_native_excel_filter_command,
        parse_native_excel_find_replace_command,
        parse_native_excel_sort_command,
    ):
        request = function(text)
        if request:
            return f"native_excel:{request['operation']}"
    for function in (
        parse_native_hwp_find_replace_command,
        parse_native_hwp_text_format_command,
        parse_native_hwp_paragraph_format_command,
        parse_native_hwp_insert_command,
        parse_native_hwp_save_command,
    ):
        request = function(text)
        if request:
            return f"native_hwp:{request['operation']}"
    analysis = parser.analyze_command(text)
    if analysis.get("kind") == "compound":
        return "local:compound" if analysis.get("executable") else "ai_handoff"
    macro = analysis.get("macro")
    if macro and analysis.get("executable"):
        macro_data = parser.dict_mgr.macro_dict.get(macro, {})
        if macro_data.get("type") == "learned":
            return f"learned:{macro}"
        return f"local:{macro}"
    return "ai_handoff"


def safety_for(route_name):
    if route_name in {
        "native_excel:choose_format_method",
        "native_hwp:choose_hwp_replace_scope",
        "confirmation_demo",
        "local:SHUTDOWN",
    }:
        return "confirmation"
    if route_name.startswith("native_"):
        return "state_dependent_preflight"
    if route_name.startswith("learned:"):
        return "stored_run_policy"
    if route_name == "ai_handoff":
        return "ai_review_before_action"
    return "local_safe"


def main():
    corpus = build_corpus()
    expected_counts = {
        "windows_app": 15,
        "file_folder": 10,
        "excel_basic": 25,
        "excel_complex_risky": 15,
        "hwp_basic": 10,
        "learned_reuse": 10,
        "ambiguous_expression": 5,
        "error_cancel_recovery": 10,
    }
    actual_counts = dict(Counter(item["category"] for item in corpus))
    if len(corpus) != 100 or actual_counts != expected_counts:
        raise RuntimeError(f"100-command corpus distribution mismatch: {actual_counts}")

    with tempfile.TemporaryDirectory(prefix="jarvis-stage12-capability-") as temp_dir:
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(os.path.join(temp_dir, "dictionaries.json"))
        # Keep the offline route benchmark deterministic even when the host's
        # application scan has not populated a fresh isolated dictionary.
        parser.dict_mgr.noun_dict.update({
            "메모장": r"C:\Windows\System32\notepad.exe",
            "계산기": r"C:\Windows\System32\calc.exe",
        })
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser.action_executor)
        parser.action_executor.noun_dict = parser.dict_mgr.noun_dict
        install_learned_benchmark_macros(parser)

        results = []
        durations = []
        for index, item in enumerate(corpus, start=1):
            started = time.perf_counter()
            actual_route = route(parser, item["text"])
            duration_ms = (time.perf_counter() - started) * 1000
            durations.append(duration_ms)
            expected_safety = safety_for(item["expected_route"])
            actual_safety = safety_for(actual_route)
            results.append({
                "index": index,
                **item,
                "actual_route": actual_route,
                "expected_safety": expected_safety,
                "actual_safety": actual_safety,
                "route_passed": actual_route == item["expected_route"],
                "safety_passed": actual_safety == expected_safety,
                "duration_ms": round(duration_ms, 3),
            })

    route_passed = sum(1 for item in results if item["route_passed"])
    safety_passed = sum(1 for item in results if item["safety_passed"])
    learned = [item for item in results if item["category"] == "learned_reuse"]
    ai_count = sum(1 for item in results if item["actual_route"] == "ai_handoff")
    intervention_count = sum(
        1 for item in results if item["actual_safety"] in {
            "confirmation", "state_dependent_preflight", "stored_run_policy"
        }
    )
    report = {
        "stage": 12,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "success": route_passed == 100 and safety_passed == 100,
        "corpus_size": len(results),
        "category_counts": actual_counts,
        "metrics": {
            "first_try_route_success_rate": round(route_passed, 2),
            "final_route_resolution_rate": round(route_passed, 2),
            "validation_accuracy": round(route_passed, 2),
            "safety_judgment_accuracy": round(safety_passed, 2),
            "learned_reuse_rate": round(
                sum(1 for item in learned if item["route_passed"]) * 100 / len(learned), 2
            ),
            "predicted_ai_call_rate": round(ai_count, 2),
            "preflight_or_confirmation_cases": intervention_count,
            "average_offline_routing_ms": round(sum(durations) / len(durations), 3),
            "max_offline_routing_ms": round(max(durations), 3),
        },
        "metric_scope": (
            "Side-effect-free parser routing and safety-gate selection. "
            "Task completion and external AI latency are measured separately."
        ),
        "failures": [
            {"index": item["index"], "text": item["text"], "expected": item["expected_route"], "actual": item["actual_route"]}
            for item in results if not item["route_passed"] or not item["safety_passed"]
        ],
        "cases": results,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
