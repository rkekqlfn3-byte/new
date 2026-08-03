import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from verification.product_goal_acceptance import (
    MANUAL_CHECK_IDS,
    PROBE_SPECS,
    evaluate_acceptance,
    validate_probe_report,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
TEST_SOURCE = {
    "identity_version": 1,
    "commit": "a" * 40,
    "branch": "test",
    "dirty": True,
    "tree_hash": "b" * 64,
    "changed_paths_hash": "c" * 64,
}


def probe_report(spec, *, success=True, generated_at=NOW, checks=True):
    report = {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": dict(TEST_SOURCE),
        "probe": spec["probe"],
        "success": success,
        "user_documents_modified": False,
        "paths_or_contents_reported": False,
        "result": {
            "status": "passed" if success else "failed",
            "owned_fixture_only": True,
            "user_process_protected": True,
            "checks": {"contract_verified": checks},
            "owned_process_cleanup_verified": True,
        },
    }
    if spec.get("report_format"):
        report["report_format"] = spec["report_format"]
    if spec.get("required_targets"):
        report["results"] = {
            target: {
                "status": "passed",
                "owned_fixture_only": True,
                "user_process_protected": True,
            }
            for target in spec["required_targets"]
        }
        for target, check_names in spec.get(
            "required_target_checks", {}
        ).items():
            for check_name in check_names:
                report["results"][target][check_name] = True
    for check_name in spec.get("required_checks", ()):
        report["result"]["checks"][check_name] = True
    if spec["probe"] == "prototype1_stage8_four_app_stability":
        report["prototype_1_0_ready"] = True
    return report


def hwp_security_module_block_report(spec, *, generated_at=NOW):
    report = probe_report(spec, success=False, generated_at=generated_at)
    report["result"] = {
        "status": "blocked",
        "stage": "prepare_approval_state",
        "error_type": "environment_error",
        "exception_type": "HwpSecurityModuleUnavailable",
        "retryable": True,
        "user_process_protected": True,
        "owned_process_cleanup_verified": True,
        "diagnostic_context": {
            "environment_component": "hwp_automation_security_module",
            "setup_guide_url": "https://developer.hancom.com/hwpautomation",
            "registry_location": r"HKCU\Software\HNC\HwpAutomation\Modules",
            "automatic_install_attempted": False,
        },
    }
    return report


class ProductGoalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gate_supports_direct_python_script_invocation(self):
        script = Path(__file__).resolve().parents[2] / "verification" / "product_goal_acceptance.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=script.parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--automated-only", completed.stdout)

    def _write_all_probes(self):
        for spec in PROBE_SPECS.values():
            (self.root / spec["file"]).write_text(
                json.dumps(probe_report(spec)), encoding="utf-8"
            )

    def test_probe_validation_rejects_stale_or_failed_nested_check(self):
        spec = PROBE_SPECS["workflow_hwp"]
        stale = probe_report(
            spec, generated_at=NOW - timedelta(hours=169)
        )
        self.assertIn(
            "probe_report_stale",
            validate_probe_report(stale, spec, now=NOW, max_age_hours=168),
        )
        failed = probe_report(spec, checks=False)
        self.assertIn(
            "probe_check_failed",
            validate_probe_report(failed, spec, now=NOW, max_age_hours=168),
        )

    def test_probe_validation_rejects_missing_or_different_source_identity(self):
        spec = PROBE_SPECS["workflow_hwp"]
        missing = probe_report(spec)
        missing.pop("source")
        self.assertIn(
            "probe_source_identity_missing",
            validate_probe_report(
                missing,
                spec,
                now=NOW,
                max_age_hours=168,
                expected_source=TEST_SOURCE,
            ),
        )

        changed = probe_report(spec)
        changed["source"]["tree_hash"] = "d" * 64
        self.assertIn(
            "probe_source_tree_hash_mismatch",
            validate_probe_report(
                changed,
                spec,
                now=NOW,
                max_age_hours=168,
                expected_source=TEST_SOURCE,
            ),
        )

    def test_probe_validation_rejects_success_when_one_app_was_skipped(self):
        spec = PROBE_SPECS["native_excel_hwp"]
        report = probe_report(spec)
        report.pop("result")
        report["results"] = {
            "excel": {"status": "passed", "owned_fixture_only": True},
            "hwp": {"status": "skipped_user_processes_running"},
        }

        self.assertIn(
            "one_or_more_probe_targets_not_passed",
            validate_probe_report(report, spec, now=NOW, max_age_hours=168),
        )

    def test_probe_validation_requires_all_named_targets_and_hwp_learning_check(self):
        spec = PROBE_SPECS["native_excel_hwp"]
        missing_target = probe_report(spec)
        del missing_target["results"]["excel"]
        self.assertIn(
            "required_probe_target_missing_or_failed",
            validate_probe_report(
                missing_target, spec, now=NOW, max_age_hours=168
            ),
        )

        missing_check = probe_report(spec)
        del missing_check["results"]["hwp"][
            "learned_font_default_readback_verified"
        ]
        self.assertIn(
            "required_target_check_missing_or_failed",
            validate_probe_report(
                missing_check, spec, now=NOW, max_age_hours=168
            ),
        )

    def test_user_learning_probe_requires_conflict_resolution_evidence(self):
        spec = PROBE_SPECS["user_learning"]
        report = probe_report(spec)
        del report["result"]["checks"]["replacement_candidate_explained"]

        self.assertIn(
            "required_probe_check_missing_or_failed",
            validate_probe_report(report, spec, now=NOW, max_age_hours=168),
        )

    def test_automated_evidence_never_claims_manual_acceptance(self):
        self._write_all_probes()
        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={
                "status": "passed", "tests_run": 806,
                "failures": 0, "errors": 0, "skipped": 3,
            },
            now=NOW,
            expected_source=TEST_SOURCE,
        )

        self.assertTrue(report["automated_passed"])
        self.assertFalse(report["manual_passed"])
        self.assertEqual(
            "automated_pass_manual_pending", report["overall_status"]
        )
        self.assertEqual(
            set(MANUAL_CHECK_IDS), set(report["manual_acceptance"])
        )
        self.assertEqual(
            "not_automated",
            report["goal_axes"]["novice_accessibility"]["automated_status"],
        )

    def test_only_explicit_manual_passes_complete_acceptance(self):
        self._write_all_probes()
        manual = {
            "checks": {
                check_id: {
                    "status": "passed",
                    "performed_at": "2026-07-18T15:00:00+0900",
                    "notes": "must not be copied",
                }
                for check_id in MANUAL_CHECK_IDS
            }
        }
        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={"status": "passed"},
            manual_evidence=manual,
            now=NOW,
            expected_source=TEST_SOURCE,
        )

        self.assertTrue(report["manual_passed"])
        self.assertEqual("accepted", report["overall_status"])
        self.assertNotIn(
            "must not be copied", json.dumps(report, ensure_ascii=False)
        )

    def test_missing_probe_fails_only_affected_axes_and_overall_gate(self):
        self._write_all_probes()
        (self.root / PROBE_SPECS["workflow_hwp"]["file"]).unlink()

        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={"status": "passed"},
            now=NOW,
            expected_source=TEST_SOURCE,
        )

        self.assertFalse(report["automated_passed"])
        self.assertEqual("automated_failed", report["overall_status"])
        self.assertEqual(
            "failed", report["goal_axes"]["cross_app_workflow"]["automated_status"]
        )
        self.assertEqual(
            "passed", report["goal_axes"]["failure_classification"]["automated_status"]
        )

    def test_exact_hwp_setup_block_is_reported_without_becoming_a_pass(self):
        self._write_all_probes()
        spec = PROBE_SPECS["workflow_hwp"]
        (self.root / spec["file"]).write_text(
            json.dumps(hwp_security_module_block_report(spec)),
            encoding="utf-8",
        )

        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={"status": "passed"},
            now=NOW,
            expected_source=TEST_SOURCE,
        )

        self.assertFalse(report["automated_passed"])
        self.assertTrue(report["environment_blocked"])
        self.assertEqual("environment_blocked", report["overall_status"])
        probe = report["owned_fixture_probes"]["workflow_hwp"]
        self.assertEqual("environment_blocked", probe["status"])
        self.assertEqual(
            "hwp_automation_security_module",
            probe["environment"]["component"],
        )
        self.assertEqual(
            "environment_blocked",
            report["goal_axes"]["cross_app_workflow"]["automated_status"],
        )
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("registry_location", rendered)
        self.assertNotIn("message", rendered)

    def test_hwp_environment_block_requires_exact_safe_diagnostics(self):
        self._write_all_probes()
        spec = PROBE_SPECS["workflow_hwp"]
        unsafe = hwp_security_module_block_report(spec)
        unsafe["result"]["diagnostic_context"][
            "automatic_install_attempted"
        ] = True
        (self.root / spec["file"]).write_text(
            json.dumps(unsafe),
            encoding="utf-8",
        )

        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={"status": "passed"},
            now=NOW,
            expected_source=TEST_SOURCE,
        )

        self.assertFalse(report["environment_blocked"])
        self.assertEqual("automated_failed", report["overall_status"])
        self.assertEqual(
            "failed",
            report["owned_fixture_probes"]["workflow_hwp"]["status"],
        )


if __name__ == "__main__":
    unittest.main()
