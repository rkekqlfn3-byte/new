"""Owned-fixture live verification for the Stage 10 document workflow."""

from __future__ import annotations

import argparse
import gc
import json
import multiprocessing
import queue
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import psutil

from engine.workflows import WorkflowExecutor
from engine.workflows.business_workflow import file_fingerprint


REPORT_PATHS = {
    report_format: Path(__file__).with_name(
        f"prototype11_stage10_{report_format}_report.json"
    )
    for report_format in ("word", "hwp", "both")
}
OFFICE_PROCESSES = frozenset({
    "excel.exe", "winword.exe", "powerpnt.exe", "hwp.exe", "hwp64.exe"
})


def _process_ids():
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() in OFFICE_PROCESSES:
                result.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _wait_for_cleanup(baseline, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not (_process_ids() - baseline):
            return True
        time.sleep(0.2)
    return not (_process_ids() - baseline)


def _stop_created_processes(baseline):
    for pid in _process_ids() - baseline:
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(5)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass


def _create_source(path):
    import win32com.client

    application = workbook = sheet = cost_sheet = None
    try:
        application = win32com.client.DispatchEx("Excel.Application")
        application.Visible = False
        application.DisplayAlerts = False
        workbook = application.Workbooks.Add()
        sheet = workbook.Worksheets.Item(1)
        sheet.Name = "매출"
        sheet.Range("A1:F6").Value2 = (
            ("항목ID", "지역", "담당자", "수량", "매출", "고객ID"),
            (1, "서울", "김", 10, 1200000, 101),
            (2, "부산", "이", 7, 900000, 102),
            (3, "서울", "박", 12, 1500000, 103),
            (4, "대전", "최", 5, 600000, 104),
            (1, "서울", "정", 2, 300000, 101),
        )
        cost_sheet = workbook.Worksheets.Add(After=sheet)
        cost_sheet.Name = "비용"
        cost_sheet.Range("A1:E5").Value2 = (
            ("참조항목ID", "항목", "담당자", "비용", "구매자ID"),
            (1, "인건비", "김", 300000, 101),
            (1, "교통비", "정", 200000, 102),
            (3, "임대료", "박", 200000, 103),
            (4, "광고비", "최", 150000, 104),
        )
        workbook.SaveAs(str(path), FileFormat=51)
    finally:
        cost_sheet = None
        sheet = None
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:
                pass
        workbook = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()


def _verify_word(path, expected_formatting=None):
    import win32com.client

    application = document = None
    try:
        application = win32com.client.DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        document = application.Documents.Open(
            str(path), ReadOnly=True, AddToRecentFiles=False
        )
        text = str(document.Content.Text or "")
        verified = int(document.Paragraphs.Count) > 0 and "핵심 지표" in text
        expected = dict(expected_formatting or {})
        if "emphasis_style" in expected:
            actual_bold = bool(int(document.Content.Font.Bold))
            verified = verified and actual_bold == (
                expected["emphasis_style"] == "bold"
            )
        if "font_scale" in expected:
            expected_size = 14.0 if expected["font_scale"] == "larger" else 10.0
            verified = verified and abs(
                float(document.Content.Font.Size) - expected_size
            ) <= 0.01
        if "paragraph_align" in expected:
            alignments = {"left": 0, "center": 1, "right": 2, "justify": 3}
            verified = verified and int(
                document.Content.ParagraphFormat.Alignment
            ) == alignments[expected["paragraph_align"]]
        return verified
    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass
        document = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()


def _verify_powerpoint(path, expected_formatting=None):
    import win32com.client

    application = presentation = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = application.Presentations.Open(
            str(path), ReadOnly=True, Untitled=False, WithWindow=False
        )
        slide_count = int(presentation.Slides.Count)
        expected = dict(expected_formatting or {})
        formatting_verified = True
        for slide_index in range(1, slide_count + 1):
            slide = presentation.Slides.Item(slide_index)
            ranges = (
                ("title", slide.Shapes.Title.TextFrame.TextRange),
                (
                    "body",
                    slide.Shapes.Placeholders.Item(2).TextFrame.TextRange,
                ),
            )
            for role, text_range in ranges:
                if "emphasis_style" in expected:
                    formatting_verified = formatting_verified and (
                        bool(int(text_range.Font.Bold))
                        == (expected["emphasis_style"] == "bold")
                    )
                if "font_scale" in expected:
                    expected_size = {
                        "larger": {"title": 36.0, "body": 24.0},
                        "smaller": {"title": 24.0, "body": 14.0},
                    }[expected["font_scale"]][role]
                    formatting_verified = formatting_verified and (
                        abs(float(text_range.Font.Size) - expected_size) <= 0.01
                    )
                if "paragraph_align" in expected:
                    alignment = {
                        "left": 1,
                        "center": 2,
                        "right": 3,
                        "justify": 4,
                    }[expected["paragraph_align"]]
                    formatting_verified = formatting_verified and (
                        int(text_range.ParagraphFormat.Alignment) == alignment
                    )
        return {
            "slide_count": slide_count == 5,
            "formatting": formatting_verified,
        }
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        presentation = None
        if application is not None:
            try:
                application.Quit()
            except Exception:
                pass
        application = None
        gc.collect()


def _publish_progress(progress, stage):
    if progress is None:
        return
    try:
        progress.put_nowait(str(stage))
    except queue.Full:
        pass


def _structured_failure(stage, error):
    unavailable = isinstance(error, (ImportError, ModuleNotFoundError))
    status = "unavailable" if unavailable else str(
        getattr(error, "status", "") or "failed"
    )
    result = {
        "status": status,
        "stage": stage,
        "error_type": str(
            getattr(error, "error_type", type(error).__name__)
        ),
        "exception_type": type(error).__name__,
        "message": str(error),
        "retryable": bool(getattr(error, "retryable", False)),
        "user_process_protected": True,
    }
    diagnostic = getattr(error, "diagnostic_context", None)
    if isinstance(diagnostic, dict):
        safe_diagnostic = {}
        if diagnostic.get("environment_component") == (
            "hwp_automation_security_module"
        ):
            safe_diagnostic["environment_component"] = (
                "hwp_automation_security_module"
            )
        if diagnostic.get("setup_guide_url") == (
            "https://developer.hancom.com/hwpautomation"
        ):
            safe_diagnostic["setup_guide_url"] = (
                "https://developer.hancom.com/hwpautomation"
            )
        if diagnostic.get("registry_location") == (
            r"HKCU\Software\HNC\HwpAutomation\Modules"
        ):
            safe_diagnostic["registry_location"] = (
                r"HKCU\Software\HNC\HwpAutomation\Modules"
            )
        if isinstance(diagnostic.get("automatic_install_attempted"), bool):
            safe_diagnostic["automatic_install_attempted"] = diagnostic[
                "automatic_install_attempted"
            ]
        if safe_diagnostic:
            result["diagnostic_context"] = safe_diagnostic
    return result


def _owned_probe(report_format="word", progress=None):
    import pythoncom

    baseline = _process_ids()
    if baseline:
        return {
            "status": "skipped_user_office_running",
            "user_process_protected": True,
        }
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-stage10-workflow-"))
    source = temp_dir / f"stage10-{uuid.uuid4().hex}.xlsx"
    stage = "create_owned_excel_source"
    _publish_progress(progress, stage)
    pythoncom.CoInitialize()
    try:
        _create_source(source)
        if not _wait_for_cleanup(baseline):
            raise RuntimeError("원본 생성용 Excel 프로세스가 종료되지 않았습니다.")
        source_before = file_fingerprint(source)

        stage = "inspect_relationship_candidates"
        _publish_progress(progress, stage)
        executor = WorkflowExecutor(temp_dir / "workflow-state")
        inspection_entries_before = {
            path.name for path in temp_dir.iterdir()
        }
        relationship_inspection = executor.analyzer.relationship_candidates({
            "source_path": str(source),
        })
        inspection_entries_after = {
            path.name for path in temp_dir.iterdir()
        }
        relationship_inspection_created_nothing = (
            inspection_entries_before == inspection_entries_after
        )
        relationship_inspection_source_unchanged = (
            file_fingerprint(source) == source_before
        )

        stage = "prepare_approval_state"
        _publish_progress(progress, stage)
        expected_formatting = {
            "emphasis_style": "bold",
            "font_scale": "larger",
            "paragraph_align": "center",
        }
        state = executor.prepare(
            source,
            title="Stage 10 매출 분석",
            report_format=report_format,
            preferences=expected_formatting,
            join_plan={
                "left_sheet": "매출",
                "right_sheet": "비용",
                "left_key": "항목ID",
                "right_key": "참조항목ID",
                "join_type": "inner",
                "left_aggregation": [
                    {"column": "매출", "function": "sum"},
                    {"column": "수량", "function": "average"},
                    {"column": "담당자", "function": "count"},
                    {"column": "매출", "function": "minimum"},
                    {"column": "매출", "function": "maximum"},
                ],
                "right_aggregation": [
                    {"column": "비용", "function": "sum"},
                    {"column": "비용", "function": "average"},
                    {"column": "항목", "function": "count"},
                    {"column": "비용", "function": "minimum"},
                    {"column": "비용", "function": "maximum"},
                ],
            },
        )
        preview_created_nothing = all(
            not Path(path).exists() for path in state["output_paths"].values()
        )

        stage = "execute_requested_steps"
        _publish_progress(progress, stage)
        result = executor.start(state)
        report_kinds = (
            ("word", "hwp")
            if report_format == "both"
            else (report_format,)
        )
        output_paths = dict(result["output_paths"])
        report_paths = {
            kind: Path(
                output_paths[f"report_{kind}"]
                if report_format == "both"
                else output_paths["report"]
            )
            for kind in report_kinds
        }
        presentation_path = Path(result["output_paths"]["presentation"])

        stage = "reopen_and_verify_outputs"
        _publish_progress(progress, stage)
        word_verified = True
        if "word" in report_kinds:
            word_verified = _verify_word(
                report_paths["word"], expected_formatting
            )
        powerpoint_readback = _verify_powerpoint(
            presentation_path, expected_formatting
        )
        stored = executor.load(state["workflow_id"])
        verification_results = stored.get("verification_results", {})
        hwp_verified = True
        if "hwp" in report_kinds:
            hwp_path = report_paths["hwp"]
            hwp_verification = verification_results.get(
                "create_hwp_report", {}
            )
            hwp_verified = (
                hwp_path.suffix.casefold() == ".hwp"
                and hwp_path.is_file()
                and hwp_verification.get("format") == "hwp"
                and hwp_verification.get("content_readback") is True
                and hwp_verification.get("title_present") is True
                and int(hwp_verification.get("text_length") or 0) > 0
                and hwp_verification.get("applied_formatting")
                == expected_formatting
            )
        report_verified = bool(word_verified and hwp_verified)
        expected_steps = ["analyze_excel"]
        if "word" in report_kinds:
            expected_steps.append("create_word_report")
        if "hwp" in report_kinds:
            expected_steps.append("create_hwp_report")
        expected_steps.append("create_powerpoint_summary")
        analyzed_tables = (stored.get("work_product") or {}).get("tables", [])
        analyzed_source_tables = [
            table for table in analyzed_tables if not table.get("derived")
        ]
        analyzed_join_tables = [
            table for table in analyzed_tables if table.get("join")
        ]
        analyzed_sheet_names = {
            table.get("name") for table in analyzed_source_tables
        }
        analyzed_sheet_count = (
            stored.get("verification_results", {})
            .get("analyze_excel", {})
            .get("sheet_count")
        )
        analyzed_pivot_count = (
            stored.get("verification_results", {})
            .get("analyze_excel", {})
            .get("pivot_summary_count")
        )
        analyzed_join_count = (
            stored.get("verification_results", {})
            .get("analyze_excel", {})
            .get("join_summary_count")
        )
        join_table = (
            analyzed_join_tables[0]
            if len(analyzed_join_tables) == 1
            else {}
        )
        join_summary = dict(join_table.get("join") or {})
        raw_left_aggregations = join_summary.get("left_aggregation") or []
        left_aggregation_summaries = (
            [dict(raw_left_aggregations)]
            if isinstance(raw_left_aggregations, dict)
            else [dict(item) for item in raw_left_aggregations]
        )
        raw_aggregations = join_summary.get("right_aggregation") or []
        aggregation_summaries = (
            [dict(raw_aggregations)]
            if isinstance(raw_aggregations, dict)
            else [dict(item) for item in raw_aggregations]
        )
        checks = {
            "relationship_inspection_verified": (
                relationship_inspection == {
                    "status": "candidate_found",
                    "candidate_count": 2,
                    "candidates": [
                        {
                            "left_sheet": "비용",
                            "right_sheet": "매출",
                            "left_key": "담당자",
                            "right_key": "담당자",
                            "match_basis": "normalized_header",
                            "ambiguous": False,
                            "cardinality": "one_to_one",
                            "matched_key_count": 4,
                            "left_distinct_count": 4,
                            "right_distinct_count": 5,
                            "left_coverage": 1.0,
                            "right_coverage": 0.8,
                            "sample_limited": False,
                            "requires_preaggregation": False,
                            "confidence": "high",
                        },
                        {
                            "left_sheet": "비용",
                            "right_sheet": "매출",
                            "left_key": "구매자ID",
                            "right_key": "고객ID",
                            "match_basis": "value_overlap",
                            "ambiguous": False,
                            "cardinality": "one_to_many",
                            "matched_key_count": 4,
                            "left_distinct_count": 4,
                            "right_distinct_count": 4,
                            "left_coverage": 1.0,
                            "right_coverage": 1.0,
                            "sample_limited": False,
                            "requires_preaggregation": False,
                            "confidence": "review_required",
                        },
                    ],
                    "automatic_execution_allowed": False,
                    "raw_cell_values_stored": False,
                    "document_paths_reported": False,
                }
                and relationship_inspection_created_nothing
                and relationship_inspection_source_unchanged
            ),
            "approval_preview_created_nothing": preview_created_nothing,
            "common_model_verified": bool(stored.get("work_product")),
            "two_source_tables_and_one_join_created": (
                len(analyzed_source_tables) == 2
                and len(analyzed_join_tables) == 1
                and len(analyzed_tables) == 3
            ),
            "both_fixture_sheet_names_present": analyzed_sheet_names == {"매출", "비용"},
            "verification_sheet_count_is_two": analyzed_sheet_count == 2,
            "bounded_pivot_summaries_created": int(
                analyzed_pivot_count or 0
            ) == 2,
            "explicit_join_verified": (
                int(analyzed_join_count or 0) == 1
                and join_summary.get("join_type") == "inner"
                and join_summary.get("left_sheet") == "매출"
                and join_summary.get("right_sheet") == "비용"
                and join_summary.get("left_key") == "항목ID"
                and join_summary.get("right_key") == "참조항목ID"
                and int(join_summary.get("output_rows") or 0) == 3
            ),
            "mapped_join_keys_verified": (
                join_summary.get("left_key") == "항목ID"
                and join_summary.get("right_key") == "참조항목ID"
                and join_summary.get("left_key")
                != join_summary.get("right_key")
            ),
            "left_preaggregation_verified": (
                left_aggregation_summaries == [
                    {
                        "column": "매출",
                        "function": "sum",
                        "input_rows": 5,
                        "groups": 4,
                    },
                    {
                        "column": "수량",
                        "function": "average",
                        "input_rows": 5,
                        "groups": 4,
                    },
                    {
                        "column": "담당자",
                        "function": "count",
                        "input_rows": 5,
                        "groups": 4,
                    },
                    {
                        "column": "매출",
                        "function": "minimum",
                        "input_rows": 5,
                        "groups": 4,
                    },
                    {
                        "column": "매출",
                        "function": "maximum",
                        "input_rows": 5,
                        "groups": 4,
                    },
                ]
                and join_table.get("headers", [])[:6] == [
                    "매출/항목ID",
                    "매출/매출 합계",
                    "매출/수량 평균",
                    "매출/담당자 건수",
                    "매출/매출 최솟값",
                    "매출/매출 최댓값",
                ]
                and [row[:6] for row in join_table.get("rows", [])] == [
                    [1, 1500000, 6, 2, 300000, 1200000],
                    [3, 1500000, 12, 1, 1500000, 1500000],
                    [4, 600000, 5, 1, 600000, 600000],
                ]
            ),
            "aggregated_join_verified": (
                aggregation_summaries
                and aggregation_summaries[0].get("column") == "비용"
                and aggregation_summaries[0].get("function") == "sum"
                and int(aggregation_summaries[0].get("input_rows") or 0) == 4
                and int(aggregation_summaries[0].get("groups") or 0) == 3
                and int(join_summary.get("output_rows") or 0) == 3
            ),
            "multi_aggregation_join_verified": (
                aggregation_summaries == [
                    {
                        "column": "비용",
                        "function": "sum",
                        "input_rows": 4,
                        "groups": 3,
                    },
                    {
                        "column": "비용",
                        "function": "average",
                        "input_rows": 4,
                        "groups": 3,
                    },
                    {
                        "column": "항목",
                        "function": "count",
                        "input_rows": 4,
                        "groups": 3,
                    },
                    {
                        "column": "비용",
                        "function": "minimum",
                        "input_rows": 4,
                        "groups": 3,
                    },
                    {
                        "column": "비용",
                        "function": "maximum",
                        "input_rows": 4,
                        "groups": 3,
                    },
                ]
                and join_table.get("headers", [])[-5:] == [
                    "비용/비용 합계",
                    "비용/비용 평균",
                    "비용/항목 건수",
                    "비용/비용 최솟값",
                    "비용/비용 최댓값",
                ]
                and [row[-5:] for row in join_table.get("rows", [])] == [
                    [500000, 250000, 2, 200000, 300000],
                    [200000, 200000, 1, 200000, 200000],
                    [150000, 150000, 1, 150000, 150000],
                ]
            ),
            "all_requested_steps_succeeded": (
                stored.get("successful_steps") == expected_steps
            ),
            "requested_report_format_preserved": (
                stored.get("report_format") == report_format
            ),
            "report_content_verified": bool(report_verified),
            "powerpoint_exactly_five_slides": powerpoint_readback["slide_count"],
            "powerpoint_formatting_verified": (
                powerpoint_readback["formatting"]
                and verification_results.get(
                    "create_powerpoint_summary", {}
                ).get("applied_formatting") == expected_formatting
                and int(
                    verification_results.get(
                        "create_powerpoint_summary", {}
                    ).get("formatted_text_range_count") or 0
                ) == 10
            ),
            "expected_artifacts_created": (
                len(result.get("created_files") or []) == len(report_kinds) + 1
            ),
            "source_unchanged": file_fingerprint(source) == source_before,
            "persistent_state_completed": stored.get("status") == "completed",
            "step_verifications_recorded": len(
                stored.get("verification_results") or {}
            ) == len(expected_steps),
        }
        if report_format == "word":
            stage = "prepare_selection_scope_workflow"
            _publish_progress(progress, stage)
            scope_plan = {
                "kind": "range",
                "sheet_name": "매출",
                "address": "B1:E4",
            }
            scope_executor = WorkflowExecutor(temp_dir / "workflow-state-scope")
            scope_state = scope_executor.prepare(
                source,
                title="Stage 10 선택 범위 분석",
                report_format="word",
                preferences=expected_formatting,
                slide_count=5,
                explicit_slide_count=True,
                source_scope=scope_plan,
            )
            scope_preview_created_nothing = all(
                not Path(path).exists()
                for path in scope_state["output_paths"].values()
            )

            stage = "execute_selection_scope_workflow"
            _publish_progress(progress, stage)
            scope_result = scope_executor.start(scope_state)
            scope_report_path = Path(scope_result["output_paths"]["report"])
            scope_presentation_path = Path(
                scope_result["output_paths"]["presentation"]
            )

            stage = "verify_selection_scope_outputs"
            _publish_progress(progress, stage)
            scope_word_verified = _verify_word(
                scope_report_path, expected_formatting
            )
            scope_powerpoint = _verify_powerpoint(
                scope_presentation_path, expected_formatting
            )
            scope_stored = scope_executor.load(scope_state["workflow_id"])
            scope_tables = (
                scope_stored.get("work_product") or {}
            ).get("tables", [])
            scope_table = scope_tables[0] if len(scope_tables) == 1 else {}
            scope_verification = (
                scope_stored.get("verification_results", {})
                .get("analyze_excel", {})
            )
            checks.update({
                "selection_scope_preview_created_nothing": (
                    scope_preview_created_nothing
                ),
                "explicit_selection_scope_verified": (
                    scope_stored.get("source_scope") == scope_plan
                    and scope_verification.get("source_scope_verified") is True
                    and scope_table.get("source_scope") == scope_plan
                    and scope_table.get("name") == "매출!B1:E4"
                    and scope_table.get("headers")
                    == ["지역", "담당자", "수량", "매출"]
                    and int(scope_table.get("total_rows") or 0) == 3
                ),
                "selection_scope_cross_app_outputs_verified": (
                    scope_word_verified
                    and scope_powerpoint.get("slide_count") is True
                    and scope_powerpoint.get("formatting") is True
                    and len(scope_result.get("created_files") or []) == 2
                    and scope_stored.get("status") == "completed"
                ),
                "selection_scope_source_unchanged": (
                    file_fingerprint(source) == source_before
                ),
            })
        return {
            "status": "passed" if all(checks.values()) else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": checks,
        }
    except Exception as error:
        return _structured_failure(stage, error)
    finally:
        gc.collect()
        pythoncom.CoUninitialize()
        _wait_for_cleanup(baseline)
        _stop_created_processes(baseline)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _worker(output, progress, report_format):
    output.put(_owned_probe(report_format, progress))


def _latest_progress(progress):
    latest = None
    while True:
        try:
            latest = progress.get_nowait()
        except queue.Empty:
            return latest


def run_probe(timeout=240, report_format="word"):
    report_format = str(report_format or "word").casefold()
    if report_format not in {"word", "hwp", "both"}:
        raise ValueError("report_format must be word, hwp, or both")
    baseline = _process_ids()
    if baseline:
        result = {
            "status": "skipped_user_office_running",
            "user_process_protected": True,
        }
    else:
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=1)
        progress = context.Queue(maxsize=16)
        process = context.Process(
            target=_worker, args=(output, progress, report_format)
        )
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(5)
            _stop_created_processes(baseline)
            result = {
                "status": "failed",
                "stage": _latest_progress(progress) or "owned_fixture_timeout",
                "error_type": "TimeoutError",
                "message": "Stage 10 소유 문서 검증이 제한 시간 안에 끝나지 않았습니다.",
                "user_process_protected": True,
            }
        else:
            try:
                result = output.get(timeout=2)
            except queue.Empty:
                result = {
                    "status": "failed",
                    "stage": "owned_fixture_worker",
                    "message": "Stage 10 검증 프로세스가 결과 없이 종료되었습니다.",
                    "user_process_protected": True,
                }
    zombies = _process_ids() - baseline
    result["owned_process_cleanup_verified"] = not zombies
    if zombies:
        _stop_created_processes(baseline)
        result["status"] = "failed"
        result.setdefault("stage", "owned_process_cleanup")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe": "prototype11_stage10_document_workflow",
        "report_format": report_format,
        "success": result["status"] == "passed",
        "user_documents_modified": False,
        "paths_or_contents_reported": False,
        "result": result,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--report-format", choices=("word", "hwp", "both"), default="word"
    )
    args = parser.parse_args(argv)
    report = run_probe(args.timeout, args.report_format)
    output_path = args.output or REPORT_PATHS[args.report_format]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
