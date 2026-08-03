"""Failure-injection verification for Stage 12 privacy-bounded diagnosis."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from engine.diagnostics import DiagnosticIncidentManager
from engine.execution_runtime import ExecutionController
from verification.source_identity import source_identity

REPORT_PATH = Path(__file__).with_name("prototype11_stage12_report.json")


def _binary_signature():
    path = Path(sys.executable)
    try:
        stat = path.stat()
        return {"size": stat.st_size, "modified_ns": stat.st_mtime_ns}
    except OSError:
        return None


def run_probe():
    secrets = (
        r"C:\Users\Stage12\Private\customer.xlsx",
        "private-user@example.com",
        "문서 안의 고객 주민번호 900101-1234567",
    )
    binary_before = _binary_signature()
    with tempfile.TemporaryDirectory(prefix="jarvis-stage12-diagnosis-") as temp_dir:
        root = Path(temp_dir)
        incident_path = root / "diagnostic_incidents.json"
        controller = ExecutionController(root / "execution_diagnostics.json")

        for index in range(2):
            execution_id = controller.begin(
                f'{secrets[0]}에서 "{secrets[2]}"를 수정해줘',
                {
                    "mode": "edit",
                    "edit_session_id": secrets[1],
                    "document_fingerprint": secrets[0],
                },
            )
            controller.event(
                "workflow_step",
                "failed",
                {
                    "failed_step": "create_powerpoint_summary",
                    "secret": secrets[2],
                },
            )
            controller.finish(
                False,
                "failed",
                error=f"{secrets[1]} {secrets[0]} {secrets[2]}",
                extra={
                    "execution_id": execution_id,
                    "verified": False,
                    "error_type": "execution_error",
                    "failed_step": "create_powerpoint_summary",
                    "retryable": True,
                    "result": {
                        "success": False,
                        "action": "business_workflow",
                        "target": secrets[0],
                        "verified": False,
                        "status": "failed",
                        "error_type": "execution_error",
                        "failed_step": "create_powerpoint_summary",
                        "retryable": True,
                        "data": {
                            "operation": "create_business_workflow",
                            "app_type": "powerpoint",
                            "document_name": secrets[0],
                        },
                    },
                },
                expected_execution_id=execution_id,
            )

        serialized = incident_path.read_text(encoding="utf-8")
        execution_serialized = (root / "execution_diagnostics.json").read_text(
            encoding="utf-8"
        )
        reloaded = DiagnosticIncidentManager(incident_path)
        incident = reloaded.latest()
        report = reloaded.report(incident["incident_id"])
        proposal = report["remediation"]
        applications = report["environment"].get("applications", {})
        checks = {
            "failure_was_collected": incident is not None,
            "duplicate_failures_grouped": incident["occurrence_count"] == 2,
            "failed_step_preserved": (
                report["where"]["failed_step"] == "create_powerpoint_summary"
            ),
            "cause_candidates_created": bool(
                report["why"].get("cause_candidates")
            ),
            "reproduction_is_synthetic": report["reproduction"].get(
                "synthetic_fixture_required"
            ) is True,
            "office_versions_collected_without_launch": all(
                name in applications
                for name in ("excel", "word", "powerpoint", "hwp")
            ),
            "raw_command_path_content_absent": all(
                secret not in serialized and secret not in execution_serialized
                for secret in secrets
            ),
            "proposal_requires_separate_worktree": proposal.get(
                "worktree_required"
            ) is True,
            "proposal_requires_test_first": proposal.get(
                "test_first_required"
            ) is True,
            "proposal_requires_full_regression": proposal.get(
                "full_regression_required"
            ) is True,
            "build_requires_separate_approval": proposal.get(
                "build_requires_separate_approval"
            ) is True,
            "no_automatic_code_change": (
                proposal.get("automatic_execution_allowed") is False
                and incident.get("automatic_code_change") is False
            ),
            "no_running_binary_mutation": (
                proposal.get("running_binary_mutation_allowed") is False
                and incident.get("running_binary_mutated") is False
                and _binary_signature() == binary_before
            ),
            "no_worktree_or_build_created": not any(
                "worktree" in item.name.casefold() or item.name.casefold() == "dist"
                for item in root.iterdir()
            ),
        }
        success = all(checks.values())
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source_identity(),
            "probe": "prototype11_stage12_self_diagnosis",
            "success": success,
            "failure_injection_only": True,
            "user_documents_modified": False,
            "user_applications_started": False,
            "paths_or_contents_reported": False,
            "automatic_fix_executed": False,
            "new_executable_built": False,
            "result": {
                "status": "passed" if success else "failed",
                "incident_id": incident["incident_id"],
                "checks": checks,
            },
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
