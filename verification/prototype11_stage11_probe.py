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

from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.stage10 import StructuredWorkflowIntentAnalyzer
from engine.edit_mode.window_layout import DocumentWindowActivator
from engine.learning import (
    BusinessWorkflowSkillManager,
    UserPreferenceLearningManager,
)
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

        stage = "verify_conflicting_preference_resolution"
        tone_candidate = None
        for index in range(1, 4):
            tone_candidate = reloaded.record_evidence(
                "report_tone",
                "formal",
                scope_kind="workflow",
                scope_id="business_report",
                evidence_id=f"stage11-tone-formal-{index}",
            )
        reloaded.activate(tone_candidate["candidate_id"], expected_value="formal")
        replacement = None
        for index in range(1, 10):
            replacement = reloaded.record_evidence(
                "report_tone",
                "friendly",
                scope_kind="workflow",
                scope_id="business_report",
                evidence_id=f"stage11-tone-friendly-{index}",
            )
        active_before_replacement = reloaded.resolve(
            "report_tone", workflow_id="business_report"
        )
        replacement_explained = bool(
            replacement
            and replacement.get("needs_confirmation")
            and replacement.get("conflict_state") == "replacement_candidate"
            and replacement.get("current_active_value") == "formal"
            and replacement.get("proposed_value") == "friendly"
        )
        replacement_dismissed = reloaded.dismiss(replacement["candidate_id"])
        active_after_dismissal = reloaded.resolve(
            "report_tone", workflow_id="business_report"
        )
        renewed = None
        for index in range(10, 13):
            renewed = reloaded.record_evidence(
                "report_tone",
                "friendly",
                scope_kind="workflow",
                scope_id="business_report",
                evidence_id=f"stage11-tone-friendly-{index}",
            )
        replacement_activation = reloaded.activate(
            renewed["candidate_id"], expected_value="friendly"
        )
        active_after_replacement = reloaded.resolve(
            "report_tone", workflow_id="business_report"
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
        first_report_fingerprint = file_fingerprint(report_path)
        first_presentation_fingerprint = file_fingerprint(presentation_path)

        stage = "approve_and_replay_content_free_workflow_skill"
        workflow_skills = BusinessWorkflowSkillManager(
            temp_dir / "business-workflow-skills.json"
        )
        workflow_candidate = workflow_skills.record_verified_success(
            result,
            evidence_id=result["workflow_id"],
        )
        workflow_skill_inactive_before_approval = (
            workflow_skills.active_skill() is None
        )
        workflow_skill_store = Path(workflow_skills.path).read_text(
            encoding="utf-8"
        )
        workflow_skill_content_free = (
            str(source) not in workflow_skill_store
            and source.name not in workflow_skill_store
            and str(report_path) not in workflow_skill_store
            and "work_product" not in workflow_skill_store
        )
        active_workflow_skill = workflow_skills.activate(
            workflow_candidate["candidate_id"]
        )
        workflow_template = active_workflow_skill["template"]
        contextual_intent = StructuredWorkflowIntentAnalyzer().analyze(
            "이거 보고서랑 7장짜리 발표자료 만들어줘",
            {"app_type": "excel"},
        )
        replay_plan = executor.prepare(
            source,
            title="Stage 11 재사용 스킬 검증",
            preferences=preferences,
            slide_count=workflow_template["slide_count"],
            report_format=workflow_template["report_format"],
        )
        workflow_skill_fresh_plan = bool(
            replay_plan.get("status") == "approval_required"
            and set(replay_plan.get("output_paths") or {})
            == set(result.get("output_paths") or {})
            and all(
                replay_plan["output_paths"][name] != result["output_paths"][name]
                for name in replay_plan["output_paths"]
            )
        )
        replay_result = executor.start(replay_plan)
        replay_report_path = Path(replay_result["output_paths"]["report"])
        replay_presentation_path = Path(
            replay_result["output_paths"]["presentation"]
        )

        stage = "reopen_outputs"
        word_verified = _verify_word(report_path, word_preferences)
        slide_count = _powerpoint_slide_count(presentation_path)
        replay_word_verified = _verify_word(
            replay_report_path,
            word_preferences,
        )
        replay_slide_count = _powerpoint_slide_count(
            replay_presentation_path
        )
        if not _wait_for_cleanup(baseline):
            raise RuntimeError(
                "산출물 재열기 검증용 숨김 Office 프로세스가 종료되지 않았습니다."
            )
        stage = "open_and_focus_recent_verified_artifacts"
        artifact_intake = FileIntakeManager(open_timeout=20.0)
        artifact_activator = DocumentWindowActivator(attempts=3)
        opened_artifacts = []
        for requested_kind in ("report", "presentation"):
            artifact = executor.latest_verified_artifact(
                source,
                requested_kind,
            )
            opened = artifact_intake.connect_file(artifact["path"])
            activation = artifact_activator.activate_when_ready(
                int(opened.get("window_handle") or 0),
                timeout=3.0,
            )
            opened_artifacts.append({
                "kind": artifact["artifact_kind"],
                "exact_path": (
                    Path(opened.get("file_path") or "").resolve()
                    == Path(artifact["path"]).resolve()
                ),
                "app_matches": (
                    str(opened.get("app_type") or "").casefold()
                    == str(artifact.get("app_type") or "").casefold()
                ),
                "fingerprint_unchanged": (
                    file_fingerprint(artifact["path"])
                    == artifact["fingerprint"]
                ),
                "focused": bool(
                    activation.get("success")
                    and activation.get("focused")
                ),
                "window_handle_present": bool(opened.get("window_handle")),
                "activation_status": str(
                    activation.get("status") or "unknown"
                ),
                "readiness_attempts": int(
                    activation.get("readiness_attempts") or 0
                ),
            })
        stored = executor.load(state["workflow_id"])
        checks = {
            "three_observations_created_candidate": candidate_ready,
            "inactive_before_user_approval": inactive_before_approval,
            "approved_preference_persisted": preference_persisted,
            "approved_scope_recorded": resolved.get("resolved_scope") == "app",
            "conflicting_default_stayed_active_before_approval": (
                active_before_replacement
                and active_before_replacement.get("value") == "formal"
            ),
            "replacement_candidate_explained": replacement_explained,
            "replacement_cancel_kept_existing_default": bool(
                replacement_dismissed
                and active_after_dismissal
                and active_after_dismissal.get("value") == "formal"
            ),
            "replacement_needed_new_evidence_after_cancel": bool(
                renewed and renewed.get("needs_confirmation")
            ),
            "approved_replacement_recorded_previous_value": bool(
                replacement_activation.get("replaced_previous")
                and replacement_activation.get("replaced_previous_value") == "formal"
                and active_after_replacement
                and active_after_replacement.get("value") == "friendly"
            ),
            "workflow_skill_inactive_before_approval": (
                workflow_skill_inactive_before_approval
            ),
            "workflow_skill_content_free_store": workflow_skill_content_free,
            "workflow_skill_requires_approval_each_run": bool(
                workflow_template.get("requires_approval_each_run")
            ),
            "workflow_skill_fresh_output_plan": workflow_skill_fresh_plan,
            "workflow_skill_actual_replay_verified": bool(
                replay_result.get("verified")
                and replay_word_verified
                and replay_slide_count == 7
            ),
            "workflow_skill_preserved_first_outputs": bool(
                file_fingerprint(report_path) == first_report_fingerprint
                and file_fingerprint(presentation_path)
                == first_presentation_fingerprint
            ),
            "contextual_current_excel_workflow_routed": bool(
                contextual_intent
                and contextual_intent.operation == "create_business_workflow"
                and contextual_intent.params.get("contextual_current_document")
                and contextual_intent.params.get("slide_count") == 7
            ),
            "recent_verified_artifacts_opened_and_focused": bool(
                len(opened_artifacts) == 2
                and {item["kind"] for item in opened_artifacts}
                == {"word_report", "presentation"}
                and all(
                    item["exact_path"]
                    and item["app_matches"]
                    and item["fingerprint_unchanged"]
                    and item["focused"]
                    for item in opened_artifacts
                )
            ),
            "recent_word_artifact_exact_path": bool(
                opened_artifacts
                and opened_artifacts[0]["kind"] == "word_report"
                and opened_artifacts[0]["exact_path"]
            ),
            "recent_word_artifact_app_matches": bool(
                opened_artifacts
                and opened_artifacts[0]["kind"] == "word_report"
                and opened_artifacts[0]["app_matches"]
            ),
            "recent_word_artifact_fingerprint_unchanged": bool(
                opened_artifacts
                and opened_artifacts[0]["kind"] == "word_report"
                and opened_artifacts[0]["fingerprint_unchanged"]
            ),
            "recent_word_artifact_focused": bool(
                opened_artifacts
                and opened_artifacts[0]["kind"] == "word_report"
                and opened_artifacts[0]["focused"]
            ),
            "recent_presentation_artifact_exact_path": bool(
                len(opened_artifacts) > 1
                and opened_artifacts[1]["kind"] == "presentation"
                and opened_artifacts[1]["exact_path"]
            ),
            "recent_presentation_artifact_app_matches": bool(
                len(opened_artifacts) > 1
                and opened_artifacts[1]["kind"] == "presentation"
                and opened_artifacts[1]["app_matches"]
            ),
            "recent_presentation_artifact_fingerprint_unchanged": bool(
                len(opened_artifacts) > 1
                and opened_artifacts[1]["kind"] == "presentation"
                and opened_artifacts[1]["fingerprint_unchanged"]
            ),
            "recent_presentation_artifact_focused": bool(
                len(opened_artifacts) > 1
                and opened_artifacts[1]["kind"] == "presentation"
                and opened_artifacts[1]["focused"]
            ),
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
            "artifact_focus_diagnostics": [
                {
                    "kind": item["kind"],
                    "window_handle_present": item["window_handle_present"],
                    "activation_status": item["activation_status"],
                    "readiness_attempts": item["readiness_attempts"],
                }
                for item in opened_artifacts
            ],
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
