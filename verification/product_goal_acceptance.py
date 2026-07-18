"""Evaluate the JARVIS product Goal without overstating manual acceptance.

The gate combines the complete deterministic regression with recent owned-fixture
probe reports.  Human-only checks remain explicit and can never be inferred from
unit tests or COM automation.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from tests.test_runner import run_tests


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = Path(__file__).with_name("product_goal_acceptance_report.json")
MANUAL_CHECK_IDS = (
    "screen_reader",
    "voice_input",
    "novice_user_observation",
)
HWP_SECURITY_MODULE_COMPONENT = "hwp_automation_security_module"
HWP_SECURITY_MODULE_GUIDE_URL = "https://developer.hancom.com/hwpautomation"
HWP_SECURITY_MODULE_BLOCK_REASONS = frozenset(
    {
        "probe_not_successful",
        "probe_result_not_passed",
        "required_probe_check_missing_or_failed",
    }
)
PROBE_SPECS = {
    "native_excel_hwp": {
        "file": "prototype1_stage5_report.json",
        "probe": "prototype1_stage5_owned_fixture_editing",
        "required_targets": ("excel", "hwp"),
        "required_target_checks": {
            "hwp": (
                "learned_font_default_readback_verified",
                "direct_hwp_formatting_observation_verified",
            ),
        },
    },
    "native_word_powerpoint": {
        "file": "prototype1_stage6_report.json",
        "probe": "prototype1_stage6_owned_fixture_word_powerpoint_editing",
        "required_targets": ("word", "powerpoint"),
        "required_target_checks": {
            "word": (
                "collapsed_word_direct_observation_verified",
            ),
            "powerpoint": (
                "collapsed_shape_direct_observation_verified",
            ),
        },
    },
    "four_app_stability": {
        "file": "prototype1_stage8_report.json",
        "probe": "prototype1_stage8_four_app_stability",
    },
    "excel_vba": {
        "file": "prototype11_stage9_report.json",
        "probe": "prototype11_stage9_excel_vba",
    },
    "hwp_workflow_watchdog": {
        "file": "prototype11_hwp_watchdog_report.json",
        "probe": "prototype11_hwp_workflow_watchdog",
        "required_checks": (
            "bounded_timeout_or_generation",
            "generation_readback_or_typed_block",
            "partial_output_absent_on_block",
        ),
    },
    "workflow_word": {
        "file": "prototype11_stage10_word_report.json",
        "probe": "prototype11_stage10_document_workflow",
        "report_format": "word",
        "required_checks": (
            "powerpoint_formatting_verified",
            "bounded_pivot_summaries_created",
            "explicit_join_verified",
        ),
    },
    "workflow_hwp": {
        "file": "prototype11_stage10_hwp_report.json",
        "probe": "prototype11_stage10_document_workflow",
        "report_format": "hwp",
        "required_checks": ("powerpoint_formatting_verified",),
    },
    "workflow_both": {
        "file": "prototype11_stage10_both_report.json",
        "probe": "prototype11_stage10_document_workflow",
        "report_format": "both",
        "required_checks": ("powerpoint_formatting_verified",),
    },
    "user_learning": {
        "file": "prototype11_stage11_report.json",
        "probe": "prototype11_stage11_user_preference_learning",
        "required_checks": (
            "replacement_candidate_explained",
            "replacement_cancel_kept_existing_default",
            "approved_replacement_recorded_previous_value",
            "workflow_skill_content_free_store",
            "workflow_skill_fresh_output_plan",
            "workflow_skill_actual_replay_verified",
            "contextual_current_excel_workflow_routed",
            "recent_verified_artifacts_opened_and_focused",
            "recent_verified_artifact_edit_session_handoff",
            "recent_word_handoff_followup_edit_verified",
            "recent_verified_presentation_edit_session_handoff",
            "recent_presentation_handoff_followup_edit_verified",
        ),
    },
    "failure_diagnosis": {
        "file": "prototype11_stage12_report.json",
        "probe": "prototype11_stage12_self_diagnosis",
    },
}
GOAL_AXES = {
    "natural_language": {"probes": ()},
    "current_context": {
        "probes": ("native_excel_hwp", "native_word_powerpoint"),
    },
    "safe_native_execution": {
        "probes": (
            "native_excel_hwp",
            "native_word_powerpoint",
            "four_app_stability",
            "excel_vba",
            "hwp_workflow_watchdog",
        ),
    },
    "successful_work_reuse": {"probes": ("user_learning",)},
    "cross_app_workflow": {
        "probes": ("workflow_word", "workflow_hwp", "workflow_both")
    },
    "user_preference_learning": {"probes": ("user_learning",)},
    "failure_classification": {
        "probes": ("failure_diagnosis", "hwp_workflow_watchdog"),
    },
    "novice_accessibility": {"probes": ()},
}
PROBE_COMMANDS = (
    ("prototype1_stage5_probe", (), False),
    ("prototype1_stage6_probe", (), False),
    ("prototype1_stage8_probe", (), True),
    ("prototype11_stage9_probe", (), True),
    ("prototype11_hwp_watchdog_probe", (), True),
    (
        "prototype11_stage10_probe",
        (
            "--report-format", "word", "--output",
            str(Path(__file__).with_name("prototype11_stage10_word_report.json")),
        ),
        True,
    ),
    (
        "prototype11_stage10_probe",
        (
            "--report-format", "hwp", "--output",
            str(Path(__file__).with_name("prototype11_stage10_hwp_report.json")),
        ),
        True,
    ),
    (
        "prototype11_stage10_probe",
        (
            "--report-format", "both", "--output",
            str(Path(__file__).with_name("prototype11_stage10_both_report.json")),
        ),
        True,
    ),
    ("prototype11_stage11_probe", (), True),
    ("prototype11_stage12_probe", (), False),
)


def _parse_generated_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return None
    return parsed


def _all_check_values(report: Mapping[str, Any]) -> list[bool]:
    values = []
    result = report.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("checks"), Mapping):
        values.extend(value is True for value in result["checks"].values())
    return values


def _nested_values(value: Any, key: str) -> list[Any]:
    found = []
    if isinstance(value, Mapping):
        for name, item in value.items():
            if name == key:
                found.append(item)
            found.extend(_nested_values(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_nested_values(item, key))
    return found


def validate_probe_report(
    report: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    now: datetime,
    max_age_hours: int,
) -> list[str]:
    """Return content-free reasons that make one probe unacceptable."""
    reasons = []
    if report.get("probe") != spec["probe"]:
        reasons.append("probe_identity_mismatch")
    if report.get("success") is not True:
        reasons.append("probe_not_successful")
    result = report.get("result")
    if isinstance(result, Mapping) and result.get("status") != "passed":
        reasons.append("probe_result_not_passed")
    results = report.get("results")
    if isinstance(results, Mapping) and any(
        not isinstance(item, Mapping) or item.get("status") != "passed"
        for item in results.values()
    ):
        reasons.append("one_or_more_probe_targets_not_passed")
    required_targets = tuple(spec.get("required_targets", ()))
    if required_targets:
        if not isinstance(results, Mapping) or any(
            not isinstance(results.get(target), Mapping)
            or results[target].get("status") != "passed"
            for target in required_targets
        ):
            reasons.append("required_probe_target_missing_or_failed")
    required_target_checks = spec.get("required_target_checks", {})
    if isinstance(required_target_checks, Mapping):
        for target, check_names in required_target_checks.items():
            target_result = (
                results.get(target) if isinstance(results, Mapping) else None
            )
            if not isinstance(target_result, Mapping) or any(
                target_result.get(check_name) is not True
                for check_name in check_names
            ):
                reasons.append("required_target_check_missing_or_failed")
    expected_format = spec.get("report_format")
    if expected_format and report.get("report_format") != expected_format:
        reasons.append("report_format_mismatch")
    generated_at = _parse_generated_at(report.get("generated_at"))
    if generated_at is None:
        reasons.append("generated_at_invalid")
    else:
        if generated_at > now + timedelta(minutes=5):
            reasons.append("generated_at_in_future")
        elif now - generated_at > timedelta(hours=max_age_hours):
            reasons.append("probe_report_stale")
    if any(value is not False for value in _nested_values(report, "user_documents_modified")):
        reasons.append("user_document_safety_unproven")
    for key in (
        "paths_or_text_reported",
        "paths_or_code_reported",
        "paths_or_contents_reported",
        "security_setting_changed",
        "user_applications_started",
        "user_preferences_modified",
        "automatic_fix_executed",
        "new_executable_built",
    ):
        if any(value is not False for value in _nested_values(report, key)):
            reasons.append(f"unsafe_{key}")
    for key in ("owned_fixture_only", "user_process_protected", "owned_process_cleanup_verified"):
        values = _nested_values(report, key)
        if values and any(value is not True for value in values):
            reasons.append(f"{key}_unproven")
    checks = _all_check_values(report)
    if checks and not all(checks):
        reasons.append("probe_check_failed")
    result_checks = (
        result.get("checks") if isinstance(result, Mapping) else {}
    )
    result_checks = result_checks if isinstance(result_checks, Mapping) else {}
    if any(
        result_checks.get(check_name) is not True
        for check_name in spec.get("required_checks", ())
    ):
        reasons.append("required_probe_check_missing_or_failed")
    if spec["probe"] == "prototype1_stage8_four_app_stability":
        if report.get("prototype_1_0_ready") is not True:
            reasons.append("prototype_stability_unproven")
    return sorted(set(reasons))


def _test_summary(result) -> dict[str, Any]:
    return {
        "status": "passed" if result.wasSuccessful() else "failed",
        "tests_run": int(result.testsRun),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
    }


def _manual_checks(value: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    supplied = dict((value or {}).get("checks") or {})
    checks = {}
    for check_id in MANUAL_CHECK_IDS:
        item = supplied.get(check_id)
        item = dict(item) if isinstance(item, Mapping) else {}
        status = str(item.get("status") or "pending").casefold()
        if status not in {"passed", "failed", "pending"}:
            status = "pending"
        checks[check_id] = {
            "status": status,
            "performed_at": str(item.get("performed_at") or "")[:40] or None,
        }
    return checks


def _recognized_environment_block(
    probe_id: str,
    report: Mapping[str, Any] | None,
    reasons: list[str],
) -> dict[str, Any] | None:
    """Recognize one exact, safe HWP setup block without treating it as pass."""
    if (
        probe_id != "workflow_hwp"
        or not isinstance(report, Mapping)
        or set(reasons) != HWP_SECURITY_MODULE_BLOCK_REASONS
        or report.get("success") is not False
        or report.get("user_documents_modified") is not False
        or report.get("paths_or_contents_reported") is not False
    ):
        return None
    result = report.get("result")
    if not isinstance(result, Mapping):
        return None
    diagnostic = result.get("diagnostic_context")
    if not isinstance(diagnostic, Mapping):
        return None
    if (
        result.get("status") != "blocked"
        or result.get("stage") != "prepare_approval_state"
        or result.get("error_type") != "environment_error"
        or result.get("exception_type") != "HwpSecurityModuleUnavailable"
        or result.get("retryable") is not True
        or result.get("user_process_protected") is not True
        or result.get("owned_process_cleanup_verified") is not True
        or diagnostic.get("environment_component")
        != HWP_SECURITY_MODULE_COMPONENT
        or diagnostic.get("setup_guide_url")
        != HWP_SECURITY_MODULE_GUIDE_URL
        or diagnostic.get("automatic_install_attempted") is not False
    ):
        return None
    return {
        "component": HWP_SECURITY_MODULE_COMPONENT,
        "setup_guide_url": HWP_SECURITY_MODULE_GUIDE_URL,
        "retryable": True,
        "automatic_install_attempted": False,
    }


def evaluate_acceptance(
    *,
    report_dir: Path,
    test_summary: Mapping[str, Any],
    manual_evidence: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    max_probe_age_hours: int = 168,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    probes = {}
    for probe_id, spec in PROBE_SPECS.items():
        path = Path(report_dir) / spec["file"]
        reasons = []
        report = None
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            reasons = ["probe_report_missing_or_invalid"]
        if isinstance(report, Mapping):
            reasons = validate_probe_report(
                report, spec, now=now, max_age_hours=max_probe_age_hours
            )
        environment = _recognized_environment_block(
            probe_id, report if isinstance(report, Mapping) else None, reasons
        )
        probe_status = (
            "passed"
            if not reasons
            else "environment_blocked"
            if environment
            else "failed"
        )
        probe_result = {
            "status": probe_status,
            "reasons": reasons,
            "generated_at": (
                str(report.get("generated_at") or "") if isinstance(report, Mapping) else None
            ),
        }
        if environment:
            probe_result["environment"] = environment
        probes[probe_id] = probe_result

    tests_passed = test_summary.get("status") == "passed"
    axes = {}
    for axis_id, axis in GOAL_AXES.items():
        required = tuple(axis["probes"])
        failed_probes = [
            probe_id for probe_id in required
            if probes[probe_id]["status"] != "passed"
        ]
        failed_statuses = {
            probes[probe_id]["status"] for probe_id in failed_probes
        }
        if tests_passed and not failed_probes:
            automated_status = "passed"
        elif (
            tests_passed
            and failed_statuses
            and failed_statuses == {"environment_blocked"}
        ):
            automated_status = "environment_blocked"
        else:
            automated_status = "failed"
        axes[axis_id] = {
            "automated_status": automated_status,
            "required_probes": list(required),
            "failed_probes": failed_probes,
        }

    manual = _manual_checks(manual_evidence)
    automated_passed = tests_passed and all(
        item["status"] == "passed" for item in probes.values()
    ) and all(item["automated_status"] == "passed" for item in axes.values())
    environment_blocked = (
        tests_passed
        and any(
            item["status"] == "environment_blocked"
            for item in probes.values()
        )
        and all(
            item["status"] in {"passed", "environment_blocked"}
            for item in probes.values()
        )
        and all(
            item["automated_status"] in {"passed", "environment_blocked"}
            for item in axes.values()
        )
    )
    manual_passed = all(item["status"] == "passed" for item in manual.values())
    if not automated_passed:
        overall_status = (
            "environment_blocked"
            if environment_blocked
            else "automated_failed"
        )
    elif not manual_passed:
        overall_status = "automated_pass_manual_pending"
    else:
        overall_status = "accepted"
    return {
        "schema_version": 2,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "goal_axes": axes,
        "automated_tests": dict(test_summary),
        "owned_fixture_probes": probes,
        "manual_acceptance": manual,
        "automated_passed": automated_passed,
        "environment_blocked": environment_blocked,
        "manual_passed": manual_passed,
        "overall_status": overall_status,
        "privacy": {
            "document_paths_or_contents_reported": False,
            "manual_notes_copied": False,
        },
    }


def _git_identity() -> dict[str, Any]:
    def command(*args):
        completed = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    return {
        "commit": command("rev-parse", "HEAD"),
        "dirty": bool(command("status", "--porcelain")),
    }


def refresh_probes(timeout: int) -> None:
    for module, extra, supports_timeout in PROBE_COMMANDS:
        command = [sys.executable, "-m", f"verification.{module}"]
        if supports_timeout:
            command.extend(("--timeout", str(timeout)))
        command.extend(extra)
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"owned fixture probe failed: {module}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--manual-evidence", type=Path)
    parser.add_argument("--max-probe-age-hours", type=int, default=168)
    parser.add_argument("--refresh-probes", action="store_true")
    parser.add_argument("--probe-timeout", type=int, default=240)
    parser.add_argument(
        "--automated-only",
        action="store_true",
        help="Return success after automated evidence passes while preserving manual pending status.",
    )
    args = parser.parse_args(argv)

    if args.refresh_probes:
        refresh_probes(args.probe_timeout)
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        tests = run_tests("all", verbosity=1)
    manual = None
    if args.manual_evidence:
        try:
            manual = json.loads(args.manual_evidence.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            manual = None
    report = evaluate_acceptance(
        report_dir=Path(__file__).parent,
        test_summary=_test_summary(tests),
        manual_evidence=manual,
        max_probe_age_hours=max(1, int(args.max_probe_age_hours)),
    )
    report["source"] = _git_identity()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["overall_status"] == "environment_blocked":
        return 3
    if not report["automated_passed"]:
        return 1
    if report["manual_passed"] or args.automated_only:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
