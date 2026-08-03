"""Run the safe, isolated maintenance failure-injection gate."""

from __future__ import annotations

import argparse
import io
import json
import unittest
from datetime import datetime
from pathlib import Path

SCENARIOS = (
    (
        "confirmation_expiry",
        "tests.integration.test_maintenance_failure_injection.ConfirmationFailureInjectionTests.test_expired_confirmation_drops_paused_execution_without_running",
    ),
    (
        "confirmation_cancel",
        "tests.integration.test_maintenance_failure_injection.ConfirmationFailureInjectionTests.test_cancelled_confirmation_never_dispatches_domain_handler",
    ),
    (
        "repeated_target_change",
        "tests.windows.test_excel_native_write.ExcelParserConfirmationFlowTests.test_repeated_target_changes_require_a_fresh_confirmation_each_time",
    ),
    (
        "atomic_fsync_failure",
        "tests.integration.test_atomic_storage.AtomicJsonStorageTests.test_fsync_failure_keeps_original_and_removes_temporary_file",
    ),
    (
        "backup_restore_lock",
        "tests.integration.test_atomic_storage.AtomicJsonStorageTests.test_valid_backup_is_returned_when_primary_restore_is_locked",
    ),
    (
        "persistent_com_busy",
        "tests.integration.test_maintenance_failure_injection.ComFailureInjectionTests.test_persistent_com_busy_exits_at_bounded_deadline",
    ),
    (
        "transient_com_busy",
        "tests.integration.test_maintenance_failure_injection.ComFailureInjectionTests.test_transient_com_busy_recovers_only_to_exact_document",
    ),
    (
        "verification_rollback_failure",
        "tests.windows.test_excel_native_write.ExcelAdapterTests.test_rollback_failure_never_turns_a_verification_failure_into_success",
    ),
    (
        "dangerous_dynamic_code",
        "tests.integration.test_dynamic_code_preflight.DynamicCodePolicyTests.test_shell_registry_credentials_and_recursive_delete_are_blocked",
    ),
)


def run_scenario(scenario_id, test_name):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromName(test_name)
    )
    return {
        "scenario_id": scenario_id,
        "test": test_name,
        "passed": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }


def run_matrix():
    scenarios = [run_scenario(*scenario) for scenario in SCENARIOS]
    return {
        "schema_version": 1,
        "probe": "maintenance_failure_injection",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "isolated_fixtures_only": True,
        "user_documents_modified": False,
        "user_applications_started": False,
        "scenario_count": len(scenarios),
        "passed_count": sum(item["passed"] for item in scenarios),
        "success": all(item["passed"] for item in scenarios),
        "scenarios": scenarios,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="선택적 JSON 보고서 경로")
    args = parser.parse_args(argv)
    report = run_matrix()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
