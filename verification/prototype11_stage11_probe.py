"""Owned-fixture verification for Stage 11 approved preference learning."""

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

from engine.learning import UserPreferenceLearningManager
from engine.workflows import WorkflowExecutor
from engine.workflows.business_workflow import file_fingerprint
from verification.prototype11_stage10_probe import (
    _create_source,
    _process_ids,
    _stop_created_processes,
    _verify_word,
    _wait_for_cleanup,
)


REPORT_PATH = Path(__file__).with_name("prototype11_stage11_report.json")


def _powerpoint_slide_count(path):
    import win32com.client

    application = presentation = None
    try:
        application = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = application.Presentations.Open(
            str(path), ReadOnly=True, Untitled=False, WithWindow=False
        )
        return int(presentation.Slides.Count)
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


def _owned_probe():
    import pythoncom

    baseline = _process_ids()
    if baseline:
        return {
            "status": "skipped_user_office_running",
            "user_process_protected": True,
        }
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-stage11-learning-"))
    source = temp_dir / f"stage11-{uuid.uuid4().hex}.xlsx"
    stage = "record_preference_evidence"
    pythoncom.CoInitialize()
    try:
        preference_path = temp_dir / "user-style-preferences.json"
        manager = UserPreferenceLearningManager(preference_path)
        candidate = None
        for index in range(1, 4):
            candidate = manager.record_evidence(
                "ppt_slide_count",
                7,
                scope_kind="app",
                scope_id="powerpoint",
                evidence_id=f"stage11-probe-{index}",
            )
        inactive_before_approval = manager.resolve(
            "ppt_slide_count", app_id="powerpoint"
        ) is None
        candidate_ready = bool(candidate and candidate.get("needs_confirmation"))

        stage = "approve_and_reload_preference"
        manager.activate(candidate["candidate_id"], expected_value=7)
        reloaded = UserPreferenceLearningManager(preference_path)
        resolved = reloaded.resolve("ppt_slide_count", app_id="powerpoint")
        preference_persisted = bool(
            resolved and resolved.get("value") == 7 and resolved.get("user_confirmed")
        )
        word_preferences = {}
        for preference, value in (
            ("emphasis_style", "bold"),
            ("font_scale", "larger"),
            ("paragraph_align", "center"),
        ):
            formatting_candidate = None
            for index in range(1, 4):
                formatting_candidate = reloaded.record_evidence(
                    preference,
                    value,
                    scope_kind="app",
                    scope_id="word",
                    evidence_id=f"stage11-probe-{preference}-{index}",
                )
            reloaded.activate(formatting_candidate["candidate_id"])
            word_preferences[preference] = reloaded.resolve(
                preference, app_id="word"
            )["value"]

        stage = "create_owned_excel_source"
        _create_source(source)
        if not _wait_for_cleanup(baseline):
            raise RuntimeError("원본 생성용 Excel 프로세스가 종료되지 않았습니다.")
        source_before = file_fingerprint(source)

        stage = "apply_approved_preference"
        preferences = {
            "ppt_slide_count": resolved["value"],
            "summary_lines": 5,
            "number_format": "thousands",
            **word_preferences,
            "_learning_metadata": {
                "ppt_slide_count": {
                    "scope": resolved["resolved_scope"],
                    "candidate_id": resolved["candidate_id"],
                }
            },
        }
        executor = WorkflowExecutor(temp_dir / "workflow-state")
        state = executor.prepare(
            source,
            title="Stage 11 선호 학습 검증",
            preferences=preferences,
            slide_count=resolved["value"],
        )
        result = executor.start(state)
        report_path = Path(result["output_paths"]["report"])
        presentation_path = Path(result["output_paths"]["presentation"])

        stage = "reopen_outputs"
        word_verified = _verify_word(report_path, word_preferences)
        slide_count = _powerpoint_slide_count(presentation_path)
        stored = executor.load(state["workflow_id"])
        checks = {
            "three_observations_created_candidate": candidate_ready,
            "inactive_before_user_approval": inactive_before_approval,
            "approved_preference_persisted": preference_persisted,
            "approved_scope_recorded": resolved.get("resolved_scope") == "app",
            "word_report_reopened_with_approved_formatting": word_verified,
            "preferred_seven_slides_created": slide_count == 7,
            "applied_preference_audited": (
                stored.get("applied_preferences", {}).get("ppt_slide_count") == 7
            ),
            "word_formatting_readback_recorded": (
                stored.get("verification_results", {})
                .get("create_word_report", {})
                .get("applied_formatting") == word_preferences
            ),
            "source_unchanged": file_fingerprint(source) == source_before,
            "workflow_completed": stored.get("status") == "completed",
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


def _worker(output):
    output.put(_owned_probe())


def run_probe(timeout=240):
    baseline = _process_ids()
    if baseline:
        result = {
            "status": "skipped_user_office_running",
            "user_process_protected": True,
        }
    else:
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=1)
        process = context.Process(target=_worker, args=(output,))
        process.start()
        process.join(timeout)
        if process.is_alive():
            process.terminate()
            process.join(5)
            _stop_created_processes(baseline)
            result = {
                "status": "failed",
                "stage": "owned_fixture_timeout",
                "error_type": "TimeoutError",
                "message": "Stage 11 소유 문서 검증이 제한 시간 안에 끝나지 않았습니다.",
                "user_process_protected": True,
            }
        else:
            try:
                result = output.get(timeout=2)
            except queue.Empty:
                result = {
                    "status": "failed",
                    "stage": "owned_fixture_worker",
                    "message": "Stage 11 검증 프로세스가 결과 없이 종료되었습니다.",
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
        "probe": "prototype11_stage11_user_preference_learning",
        "success": result["status"] == "passed",
        "user_documents_modified": False,
        "user_preferences_modified": False,
        "paths_or_contents_reported": False,
        "result": result,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)
    report = run_probe(args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
