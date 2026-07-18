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


REPORT_PATH = Path(__file__).with_name("prototype11_stage10_report.json")
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
        sheet.Range("A1:D5").Value2 = (
            ("지역", "담당자", "수량", "매출"),
            ("서울", "김", 10, 1200000),
            ("부산", "이", 7, 900000),
            ("서울", "박", 12, 1500000),
            ("대전", "최", 5, 600000),
        )
        cost_sheet = workbook.Worksheets.Add(After=sheet)
        cost_sheet.Name = "비용"
        cost_sheet.Range("A1:C4").Value2 = (
            ("항목", "분기", "비용"),
            ("인건비", "1분기", 500000),
            ("임대료", "1분기", 200000),
            ("광고비", "1분기", 150000),
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


def _verify_powerpoint(path):
    import win32com.client

    application = presentation = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = application.Presentations.Open(
            str(path), ReadOnly=True, Untitled=False, WithWindow=False
        )
        return int(presentation.Slides.Count) == 5
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

        stage = "prepare_approval_state"
        _publish_progress(progress, stage)
        executor = WorkflowExecutor(temp_dir / "workflow-state")
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
        powerpoint_verified = _verify_powerpoint(presentation_path)
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
        analyzed_sheet_names = {table.get("name") for table in analyzed_tables}
        analyzed_sheet_count = (
            stored.get("verification_results", {})
            .get("analyze_excel", {})
            .get("sheet_count")
        )
        checks = {
            "approval_preview_created_nothing": preview_created_nothing,
            "common_model_verified": bool(stored.get("work_product")),
            "two_tables_created": len(analyzed_tables) == 2,
            "both_fixture_sheet_names_present": analyzed_sheet_names == {"매출", "비용"},
            "verification_sheet_count_is_two": analyzed_sheet_count == 2,
            "all_requested_steps_succeeded": (
                stored.get("successful_steps") == expected_steps
            ),
            "requested_report_format_preserved": (
                stored.get("report_format") == report_format
            ),
            "report_content_verified": bool(report_verified),
            "powerpoint_exactly_five_slides": powerpoint_verified,
            "expected_artifacts_created": (
                len(result.get("created_files") or []) == len(report_kinds) + 1
            ),
            "source_unchanged": file_fingerprint(source) == source_before,
            "persistent_state_completed": stored.get("status") == "completed",
            "step_verifications_recorded": len(
                stored.get("verification_results") or {}
            ) == len(expected_steps),
        }
        return {
            "status": "passed" if all(checks.values()) else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": checks,
        }
    except Exception as error:
        return {
            "status": "unavailable" if isinstance(error, (ImportError, ModuleNotFoundError)) else "failed",
            "stage": stage,
            "error_type": type(error).__name__,
            "message": str(error),
            "user_process_protected": True,
        }
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
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--report-format", choices=("word", "hwp", "both"), default="word"
    )
    args = parser.parse_args(argv)
    report = run_probe(args.timeout, args.report_format)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
