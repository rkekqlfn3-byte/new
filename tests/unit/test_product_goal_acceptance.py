import json
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


def probe_report(spec, *, success=True, generated_at=NOW, checks=True):
    report = {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%S%z"),
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
    for check_name in spec.get("required_checks", ()):
        report["result"]["checks"][check_name] = True
    if spec["probe"] == "prototype1_stage8_four_app_stability":
        report["prototype_1_0_ready"] = True
    return report


class ProductGoalAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

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

    def test_automated_evidence_never_claims_manual_acceptance(self):
        self._write_all_probes()
        report = evaluate_acceptance(
            report_dir=self.root,
            test_summary={
                "status": "passed", "tests_run": 806,
                "failures": 0, "errors": 0, "skipped": 3,
            },
            now=NOW,
        )

        self.assertTrue(report["automated_passed"])
        self.assertFalse(report["manual_passed"])
        self.assertEqual(
            "automated_pass_manual_pending", report["overall_status"]
        )
        self.assertEqual(
            set(MANUAL_CHECK_IDS), set(report["manual_acceptance"])
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
        )

        self.assertFalse(report["automated_passed"])
        self.assertEqual("automated_failed", report["overall_status"])
        self.assertEqual(
            "failed", report["goal_axes"]["cross_app_workflow"]["automated_status"]
        )
        self.assertEqual(
            "passed", report["goal_axes"]["failure_classification"]["automated_status"]
        )


if __name__ == "__main__":
    unittest.main()
