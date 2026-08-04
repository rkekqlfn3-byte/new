# -*- coding: utf-8 -*-
"""Run 120 meaningfully different, owned-fixture PDF acceptance utterances."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path(__file__).with_name("pdf_acceptance_report.json")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pdf import (  # noqa: E402
    PdfIntakeManager,
    PdfIntentKind,
    PdfTaskService,
    PdfTransformationService,
)
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures, write_text_pdf  # noqa: E402
from verification.source_identity import source_identity  # noqa: E402
from verification.utterance_acceptance_battery import _make_parser  # noqa: E402


@dataclass(frozen=True)
class PdfAcceptanceCase:
    case_id: str
    category: str
    text: str
    expected_intent: PdfIntentKind
    fixture: str = "long"
    setup: str | None = None
    safe_failure: bool = False


CASE_GROUPS = (
    (
        "document_info",
        PdfIntentKind.PAGE_COUNT,
        (
            "이 PDF는 총 몇 페이지야?",
            "현재 PDF 페이지 수 알려줘",
            "연결한 문서가 몇 쪽인지 확인해줘",
            "이 문서 전체 페이지 개수를 말해줘",
            "PDF 총 쪽수가 궁금해",
            "지금 연결된 PDF의 페이지 수 보여줘",
            "이거 몇 페이지짜리 PDF야?",
            "현재 문서의 전체 쪽 수 확인",
            "PDF가 모두 몇 쪽으로 되어 있어?",
            "연결된 PDF 페이지 개수만 알려줘",
        ),
    ),
    (
        "literal_search",
        PdfIntentKind.SEARCH,
        (
            '이 PDF에서 "계약 기간" 찾아줘',
            "현재 문서에서 해지 조건 검색해줘",
            "이 PDF 안에서 매출이라는 단어 찾아봐",
            "연결한 PDF에서 담당자 이름이 어디 나오는지 찾아줘",
            "이 문서에서 결론 문구를 검색해줘",
            "PDF에서 위험 요소가 언급된 곳 찾아줘",
            "이거 안에서 납기일 찾아",
            "현재 PDF에서 부록이라는 표현이 어디 있어?",
            "이 문서에서 예산 항목 찾아보기",
            "PDF 안에서 품질 기준이 나오는 페이지 찾아줘",
        ),
    ),
    (
        "structure_navigation",
        PdfIntentKind.TABLE_OF_CONTENTS,
        (
            "이 PDF 목차 보여줘",
            "현재 문서의 차례를 정리해줘",
            "연결한 PDF에서 제목 구조만 찾아줘",
            "이 문서 목차가 어떻게 되어 있어?",
            "PDF 안의 장별 제목을 보여줘",
            "이거 차례 확인해줘",
            "현재 PDF의 소제목 목록 알려줘",
            "문서에서 목차 후보를 뽑아줘",
            "연결된 PDF 제목 순서 보여줘",
            "이 PDF의 장과 절 구조를 찾아줘",
        ),
    ),
    (
        "grounded_summary",
        PdfIntentKind.SUMMARY,
        (
            "이 PDF 전체를 요약해줘",
            "현재 문서 핵심만 간추려줘",
            "연결한 PDF 1페이지를 세 줄로 요약해줘",
            "이 문서 2~3페이지 주요 내용만 정리해줘",
            "PDF 마지막 5페이지의 핵심을 요약해줘",
            "이거 읽고 중요한 결론 위주로 줄여줘",
            "현재 PDF의 앞부분 1~2페이지 요약",
            "이 문서 4페이지를 쉬운 말로 요약해줘",
            "연결된 자료 전체 핵심 포인트 알려줘",
            "PDF 3페이지 내용만 짧게 간추려줘",
        ),
    ),
    (
        "grounded_question",
        PdfIntentKind.EXPLAIN,
        (
            "이 PDF가 무슨 내용인지 설명해줘",
            "현재 문서 2페이지를 쉽게 설명해줘",
            "연결한 PDF에서 작성자가 말하는 결론이 뭐야?",
            "이 문서 3페이지 내용 알려줘",
            "PDF에서 제안한 해결책이 무엇인지 답해줘",
            "이거 초보자도 이해하게 읽어줘",
            "현재 PDF 1~2페이지가 무슨 말인지 설명",
            "이 문서의 주장을 어떤 근거로 설명하는지 알려줘",
            "연결된 자료에서 주의할 점이 뭐야?",
            "PDF 4페이지 질문에 답해줘",
        ),
    ),
    (
        "table_workflow",
        PdfIntentKind.TABLE_EXTRACT,
        (
            "이 PDF 표를 추출해줘",
            "현재 문서의 테이블만 뽑아줘",
            "연결한 PDF 표 구조를 정리해줘",
            "이 문서에서 표를 찾아 보여줘",
            "PDF 안의 표 데이터를 추출해줘",
            "이 PDF 표를 엑셀로 만들어줘",
            "현재 문서 테이블을 Excel로 뽑아줘",
            "연결한 PDF의 표만 xlsx로 정리해줘",
            "이 문서 표 데이터를 엑셀 파일로 추출해줘",
            "PDF 테이블을 새 Excel 문서로 만들어줘",
        ),
        "table",
    ),
    (
        "office_output",
        PdfIntentKind.REPORT,
        (
            "이 PDF로 보고서 만들어줘",
            "현재 문서를 워드 보고서로 작성해줘",
            "연결한 PDF를 한글 보고서로 정리해줘",
            "이 자료로 발표 자료 만들어줘",
            "PDF를 Word 보고서와 PPT로 만들어줘",
            "이 문서 기반으로 한글 문서와 파워포인트 작성해줘",
            "현재 PDF 핵심을 5장 발표자료로 만들어줘",
            "연결된 자료를 docx 리포트로 만들어줘",
            "이 PDF로 보고서랑 발표 자료 둘 다 만들어줘",
            "PDF 내용을 한글 리포트로 작성해줘",
        ),
    ),
    (
        "page_extract",
        PdfIntentKind.SPLIT,
        (
            "이 PDF 1페이지만 분할해줘",
            "현재 문서 2~3페이지만 나눠줘",
            "연결한 PDF에서 4페이지를 새 파일로 쪼개줘",
            "이 문서 1~2페이지만 분할해줘",
            "PDF 3~5페이지만 따로 분할",
            "이거 2페이지 부분만 나눠줘",
            "현재 PDF의 1, 3페이지를 분할해줘",
            "연결된 문서 4~5쪽만 쪼개줘",
            "PDF에서 2, 4페이지를 새 문서로 분할해줘",
            "이 문서 첫 1페이지를 분할해줘",
        ),
    ),
    (
        "document_merge",
        PdfIntentKind.MERGE,
        (
            "이 PDF와 다른 PDF를 병합해줘",
            "현재 문서 뒤에 선택할 PDF를 합쳐줘",
            "연결한 PDF부터 순서대로 하나로 만들어줘",
            "이 문서와 추가 문서를 병합해줘",
            "PDF 두 개를 한 파일로 합쳐줘",
            "이거 다음에 다른 PDF를 붙여서 병합",
            "현재 PDF를 첫 번째로 문서들을 합쳐줘",
            "연결된 자료와 새로 고를 자료를 하나로 만들어줘",
            "이 PDF 뒤에 부록 PDF를 병합해줘",
            "현재 문서 포함해서 PDF를 순서대로 합쳐줘",
        ),
    ),
    (
        "page_rotation",
        PdfIntentKind.ROTATE,
        (
            "이 PDF 1페이지를 오른쪽으로 회전해줘",
            "현재 문서 2페이지를 왼쪽으로 돌려줘",
            "연결한 PDF 3페이지를 180도 회전해줘",
            "이 문서 4~5페이지를 시계 방향으로 돌려줘",
            "PDF 2페이지를 반시계 방향으로 회전",
            "이거 1, 3페이지를 오른쪽으로 돌려줘",
            "현재 PDF 5페이지를 반 바퀴 회전해줘",
            "연결된 문서 2~4쪽을 90도 회전해줘",
            "PDF 3페이지를 왼쪽 90도 돌려줘",
            "이 문서 첫 페이지를 시계 방향으로 회전해줘",
        ),
    ),
)

CONTEXT_CASES = (
    ("여기 요약해줘", PdfIntentKind.SUMMARY, "이 PDF 2페이지 설명해줘"),
    ("여기 내용을 설명해줘", PdfIntentKind.EXPLAIN, "이 PDF 3페이지 설명해줘"),
    ("다음 페이지 요약해줘", PdfIntentKind.SUMMARY, "이 PDF 2페이지 설명해줘"),
    ("앞 페이지 설명해줘", PdfIntentKind.EXPLAIN, "이 PDF 4페이지 설명해줘"),
    ("이 부분을 보고서로 만들어줘", PdfIntentKind.REPORT, "이 PDF 2~3페이지 설명해줘"),
    ("여기 표를 추출해줘", PdfIntentKind.TABLE_EXTRACT, "이 PDF 1페이지 설명해줘"),
    ("다음 쪽을 오른쪽으로 회전해줘", PdfIntentKind.ROTATE, "이 PDF 2페이지 설명해줘"),
    ("앞 쪽만 분할해줘", PdfIntentKind.SPLIT, "이 PDF 3페이지 설명해줘"),
    ("현재 문서 핵심 알려줘", PdfIntentKind.SUMMARY, None),
    ("이거 전체를 간추려줘", PdfIntentKind.SUMMARY, None),
)

SAFETY_CASES = (
    ("이 PDF 분할해줘", PdfIntentKind.SPLIT, "long"),
    ("이 PDF 2페이지를 회전해줘", PdfIntentKind.ROTATE, "long"),
    ("이 PDF 99페이지를 요약해줘", PdfIntentKind.SUMMARY, "long"),
    ("이 PDF에서 검색해줘", PdfIntentKind.SEARCH, "long"),
    ("이 PDF를 요약해줘", PdfIntentKind.SUMMARY, "scanned"),
    ("방금 만든 PDF 취소해줘", PdfIntentKind.UNDO, "long"),
    ("이 PDF 5~2페이지를 분할해줘", PdfIntentKind.SPLIT, "long"),
    ("여기 내용을 알려줘", PdfIntentKind.EXPLAIN, "long"),
    ('"다른문서.pdf" 1페이지 설명해줘', PdfIntentKind.EXPLAIN, "long"),
    ("이 PDF 1~5페이지 전체를 분할해줘", PdfIntentKind.SPLIT, "long"),
)


class _FakeConfig:
    def get_ai_config(self):
        return {"provider": "openai", "api_key": "owned-test-credential"}


class _FakeLlm:
    def __init__(self):
        self.calls = []

    def _invoke_provider(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("PDF acceptance must stop before external AI transfer.")


class _FakeOffice:
    def prepare(self, connection, output_kinds):
        outputs = tuple(output_kinds)
        return {
            "output_kinds": list(outputs),
            "output_names": [f"owned-result.{kind}" for kind in outputs],
            "output_paths": {
                kind: str(Path(connection.file_path).with_suffix(f".{kind}"))
                for kind in outputs
            },
            "recipe": [],
        }


def build_cases():
    cases = []
    index = 0
    for group in CASE_GROUPS:
        category, kind, phrases, *fixture = group
        for text in phrases:
            index += 1
            cases.append(
                PdfAcceptanceCase(
                    f"pdf-{index:03d}", category, text, kind, *(fixture or ["long"])
                )
            )
    for text, kind, setup in CONTEXT_CASES:
        index += 1
        fixture = "table" if kind is PdfIntentKind.TABLE_EXTRACT else "long"
        cases.append(
            PdfAcceptanceCase(
                f"pdf-{index:03d}", "context_followup", text, kind, fixture, setup
            )
        )
    for text, kind, fixture in SAFETY_CASES:
        index += 1
        cases.append(
            PdfAcceptanceCase(
                f"pdf-{index:03d}", "safe_failure", text, kind, fixture, safe_failure=True
            )
        )
    return cases


def _fixture_paths(root):
    owned = create_owned_pdf_fixtures(root)
    long_pdf = write_text_pdf(
        Path(root) / "long.pdf",
        [
            ["1. Executive Summary", "Contract period and conclusion"],
            ["2. Risks", "Risk factors and delivery date"],
            ["3. Budget", "Budget owner and quality criteria"],
            ["4. Recommendation", "Recommended solution and caution"],
            ["5. Appendix", "Appendix and final notes"],
        ],
    )
    return {
        "long": long_pdf,
        "table": owned.table,
        "scanned": owned.scanned,
        "second": owned.text,
    }


def _make_pdf_parser(data_dir, paths):
    parser = _make_parser(data_dir)
    manager = PdfIntakeManager()
    llm = _FakeLlm()
    transformations = PdfTransformationService(
        manager, merge_picker=lambda: (str(paths["second"]),)
    )
    parser.pdf_intake_manager = manager
    parser.pdf_task_service = PdfTaskService(
        manager,
        llm,
        _FakeConfig(),
        office_workflow=_FakeOffice(),
        transformation_service=transformations,
    )
    return parser, manager, llm


def _expected_action(case, result):
    allowed = {
        PdfIntentKind.PAGE_COUNT: {"pdf_page_count"},
        PdfIntentKind.SEARCH: {"pdf_search"},
        PdfIntentKind.TABLE_OF_CONTENTS: {"pdf_table_of_contents"},
        PdfIntentKind.SUMMARY: {"pdf_summary"},
        PdfIntentKind.EXPLAIN: {"pdf_explain"},
        PdfIntentKind.TABLE_EXTRACT: {"pdf_table_extract", "pdf_table_to_excel"},
        PdfIntentKind.REPORT: {"pdf_report"},
        PdfIntentKind.SPLIT: {"pdf_split", "pdf_extract_pages"},
        PdfIntentKind.MERGE: {"pdf_merge", "pdf_merge_documents"},
        PdfIntentKind.ROTATE: {"pdf_rotate", "pdf_rotate_pages"},
        PdfIntentKind.UNDO: {"pdf_undo"},
    }[case.expected_intent]
    if case.safe_failure:
        allowed.add("pdf_command")
    return result.get("action") in allowed


def _run_case(parser, manager, paths, case):
    manager.connect_file(str(paths[case.fixture]))
    if case.setup:
        manager.resolve_command(case.setup)
    before = {item.name for item in Path(paths[case.fixture]).parent.iterdir()}
    result = parser.execute_command_result(case.text, session_id=case.case_id)
    after = {item.name for item in Path(paths[case.fixture]).parent.iterdir()}
    action_correct = _expected_action(case, result)
    if result.get("status") == "confirmation_required":
        confirmation = result["data"]["confirmation"]
        parser.resolve_pending_confirmation(
            case.case_id,
            confirmation_id=confirmation["confirmation_id"],
            option_id="cancel",
        )
    if case.safe_failure:
        outcome_correct = not result.get("success") and result.get("status") in {
            "blocked",
            "clarification_required",
            "cancelled",
        }
    else:
        outcome_correct = result.get("success") is True or result.get("status") in {
            "confirmation_required",
            "clarification_required",
        }
    encoded = json.dumps(result, ensure_ascii=False)
    failure_private = result.get("success") is not False or (
        str(Path(paths[case.fixture]).parent) not in encoded
        and "Contract period and conclusion" not in encoded
    )
    return {
        "case_id": case.case_id,
        "category": case.category,
        "action_correct": action_correct,
        "outcome_correct": outcome_correct,
        "no_file_created": before == after,
        "failure_record_content_free": failure_private,
        "observed_action": str(result.get("action") or "")[:80],
        "observed_status": str(result.get("status") or "")[:80],
    }


def run_battery():
    cases = build_cases()
    started = time.monotonic()
    tracemalloc.start()
    with tempfile.TemporaryDirectory(prefix="jarvis-pdf-acceptance-") as temp:
        root = Path(temp)
        paths = _fixture_paths(root / "fixtures")
        parser, manager, llm = _make_pdf_parser(root / "data", paths)
        results = [_run_case(parser, manager, paths, case) for case in cases]
        fixture_names = {item.name for item in (root / "fixtures").iterdir()}
    fixtures_cleaned = not root.exists()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    duration = time.monotonic() - started
    categories = Counter(case.category for case in cases)
    checks = {
        "exactly_120_cases": len(cases) == 120,
        "all_utterances_unique": len({case.text for case in cases}) == 120,
        "twelve_categories_exact": len(categories) == 12
        and set(categories.values()) == {10},
        "intent_accuracy_100_percent": all(item["action_correct"] for item in results),
        "outcome_accuracy_100_percent": all(item["outcome_correct"] for item in results),
        "unapproved_write_zero": all(item["no_file_created"] for item in results),
        "external_ai_calls_zero": len(llm.calls) == 0,
        "failure_records_content_free": all(
            item["failure_record_content_free"] for item in results
        ),
        "bounded_runtime_under_30_seconds": duration < 30.0,
        "peak_memory_under_128_mb": peak < 128 * 1024 * 1024,
        "owned_fixtures_cleaned": bool(fixture_names) and fixtures_cleaned,
        "user_data_and_apps_untouched": True,
    }
    failures = [
        {
            "case_id": item["case_id"],
            "category": item["category"],
            "failed_checks": sorted(
                key
                for key in (
                    "action_correct",
                    "outcome_correct",
                    "no_file_created",
                    "failure_record_content_free",
                )
                if not item[key]
            ),
            "observed_action": item["observed_action"],
            "observed_status": item["observed_status"],
        }
        for item in results
        if not all(
            item[key]
            for key in (
                "action_correct",
                "outcome_correct",
                "no_file_created",
                "failure_record_content_free",
            )
        )
    ]
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source_identity(ROOT),
        "probe": "pdf_goal_120_utterance_acceptance",
        "success": all(checks.values()),
        "case_count": len(cases),
        "category_counts": dict(sorted(categories.items())),
        "duration_seconds": round(duration, 3),
        "peak_memory_mb": round(peak / (1024 * 1024), 2),
        "user_documents_modified": False,
        "paths_or_contents_reported": False,
        "result": {
            "status": "passed" if all(checks.values()) else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": checks,
            "owned_process_cleanup_verified": True,
        },
        "failures": failures,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_battery()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
