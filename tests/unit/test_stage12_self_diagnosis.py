import json
import tempfile
import unittest
from pathlib import Path

from engine.diagnostics.self_diagnosis import (
    DiagnosticIncidentError,
    DiagnosticIncidentManager,
)
from engine.execution_runtime import ExecutionController


class Stage12SelfDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "incidents.json"
        self.environment_calls = 0

        def environment():
            self.environment_calls += 1
            return {
                "app_version": "test",
                "git_commit": "ABC123",
                "build_kind": "source",
                "python_version": "3.test",
                "os_family": "Windows",
                "os_release": "test",
                "architecture": "AMD64",
                "applications": {
                    "excel": {"installed": True, "version": "16.0"},
                    "word": {"installed": True, "version": "16.0"},
                    "powerpoint": {"installed": True, "version": "16.0"},
                    "hwp": {"installed": False, "version": None},
                },
            }

        self.manager = DiagnosticIncidentManager(
            self.path, environment_provider=environment
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def failure(**changes):
        record = {
            "execution_id": "execution-1",
            "label": "엑셀에서 고객 이름을 바꿔줘",
            "success": False,
            "status": "failed",
            "error": "native execution failed",
            "response": "명령에 실패했습니다.",
            "error_type": "execution_error",
            "verified": False,
            "retryable": False,
            "metadata": {
                "mode": "command",
                "edit_session_id": "session-1",
                "document_fingerprint": "fingerprint-1",
            },
            "events": [{
                "action": "native_execute",
                "status": "failed",
                "details": {"operation": "write", "secret": "discard-me"},
            }],
            "result": {
                "success": False,
                "message": "native execution failed",
                "action": "edit_action",
                "target": "active-selection",
                "verified": False,
                "status": "failed",
                "error_type": "execution_error",
                "failed_step": None,
                "retryable": False,
                "data": {
                    "operation": "write",
                    "app_type": "excel",
                    "document_name": "sales.xlsx",
                },
            },
        }
        record.update(changes)
        return record

    def test_success_cancel_confirmation_and_busy_are_not_incidents(self):
        for status in ("success", "cancelled", "confirmation_required", "busy"):
            with self.subTest(status=status):
                record = self.failure(status=status)
                record["success"] = status == "success"
                self.assertIsNone(self.manager.record_execution_failure(record))
        self.assertEqual([], self.manager.list_incidents())
        self.assertEqual(0, self.environment_calls)

    def test_incident_persists_only_hashes_and_structural_identifiers(self):
        secrets = (
            r"C:\Users\Alice\Private\payroll.xlsx",
            "alice@example.com",
            "고객 주민번호 900101-1234567",
        )
        record = self.failure(
            label=f'{secrets[0]}에서 "{secrets[2]}"를 수정해줘',
            error=f"{secrets[1]} / {secrets[0]} / {secrets[2]}",
            response=secrets[2],
            metadata={
                "mode": "command",
                "edit_session_id": secrets[1],
                "document_fingerprint": secrets[0],
            },
            events=[{
                "action": secrets[2],
                "status": "failed",
                "details": {"operation": secrets[2], "secret": secrets[1]},
            }],
            result={
                "success": False,
                "message": secrets[2],
                "action": secrets[2],
                "target": secrets[0],
                "status": "failed",
                "error_type": "execution_error",
                "verified": False,
                "data": {
                    "operation": secrets[2],
                    "app_type": "excel",
                    "document_name": secrets[0],
                },
            },
        )
        incident = self.manager.record_execution_failure(record)
        serialized = self.path.read_text(encoding="utf-8")
        for secret in secrets:
            self.assertNotIn(secret, serialized)
        self.assertFalse(incident["raw_content_stored"])
        self.assertFalse(incident["automatic_code_change"])
        self.assertFalse(incident["running_binary_mutated"])
        self.assertRegex(incident["command"]["input_hash"], r"^[A-F0-9]{64}$")
        self.assertTrue(incident["interpreted"]["action"].startswith("anon_"))
        self.assertEqual(".xlsx", incident["interpreted"]["document_extension"])
        self.assertNotIn("secret", incident["events"][0]["details"])

    def test_duplicate_structural_failure_is_grouped(self):
        first = self.manager.record_execution_failure(self.failure())
        second = self.manager.record_execution_failure(self.failure(
            execution_id="execution-2",
            label="다른 사용자 명령 원문",
            error="different exception text",
        ))
        self.assertEqual(first["incident_id"], second["incident_id"])
        self.assertEqual(2, second["occurrence_count"])
        self.assertEqual(1, len(self.manager.list_incidents()))
        self.assertEqual(1, self.environment_calls)

    def test_failure_categories_are_deterministic(self):
        cases = (
            ("workflow_step_failure", {"failed_step": 3}),
            ("verification_mismatch", {"error_type": "verification_error"}),
            ("context_changed", {"status": "context_changed"}),
            ("permission_or_security", {"error": "Access is denied"}),
            ("office_busy", {"error": "RPC_E_CALL_REJECTED"}),
            ("timeout", {"error_type": "timeout"}),
            ("dependency_missing", {"error": "No module named win32com"}),
            ("target_unavailable", {"error_type": "target_not_found"}),
            ("validation_block", {"error_type": "validation_error"}),
            ("execution_exception", {"error": "unexpected failure"}),
        )
        for index, (expected, changes) in enumerate(cases):
            with self.subTest(expected=expected):
                record = self.failure(execution_id=f"execution-{index}")
                record["result"] = dict(record["result"])
                record["result"]["action"] = f"category_{index}"
                record.update(changes)
                incident = self.manager.record_execution_failure(record)
                self.assertEqual(expected, incident["analysis"]["category"])

    def test_report_and_remediation_are_proposal_only(self):
        incident = self.manager.record_execution_failure(
            self.failure(failed_step=2)
        )
        report = self.manager.report(incident["incident_id"])
        remediation = report["remediation"]
        self.assertEqual(2, report["where"]["failed_step"])
        self.assertTrue(remediation["worktree_required"])
        self.assertTrue(remediation["test_first_required"])
        self.assertTrue(remediation["full_regression_required"])
        self.assertTrue(remediation["build_requires_separate_approval"])
        self.assertFalse(remediation["automatic_execution_allowed"])
        self.assertFalse(remediation["running_binary_mutation_allowed"])
        self.assertFalse(report["privacy"]["document_content_collected"])

    def test_status_health_and_reload(self):
        incident = self.manager.record_execution_failure(self.failure())
        resolved = self.manager.set_status(incident["incident_id"], "resolved")
        self.assertEqual("resolved", resolved["status"])
        with self.assertRaises(DiagnosticIncidentError):
            self.manager.set_status(incident["incident_id"], "invalid")
        reloaded = DiagnosticIncidentManager(
            self.path, environment_provider=lambda: {}
        )
        self.assertEqual("resolved", reloaded.latest()["status"])
        health = reloaded.health_summary()
        self.assertEqual(1, health["incident_groups"])
        self.assertEqual(0, health["open_incidents"])
        self.assertFalse(health["automatic_code_change"])

    def test_retention_keeps_only_the_latest_bounded_groups(self):
        manager = DiagnosticIncidentManager(
            self.path, max_incidents=10, environment_provider=lambda: {}
        )
        for index in range(12):
            record = self.failure(execution_id=f"execution-{index}")
            record["result"] = dict(record["result"], action=f"action_{index}")
            manager.record_execution_failure(record)
        incidents = manager.list_incidents(limit=20)
        self.assertEqual(10, len(incidents))
        actions = {item["interpreted"]["action"] for item in incidents}
        self.assertNotIn("action_0", actions)
        self.assertNotIn("action_1", actions)

    def test_legacy_execution_log_and_backup_are_migrated_without_raw_text(self):
        path = Path(self.temp_dir.name) / "execution_diagnostics.json"
        secret = r"C:\Users\Alice\Private\legacy.xlsx 고객 원문"
        legacy = {
            "records": [{
                "execution_id": "legacy-1",
                "label": secret,
                "success": False,
                "status": "failed",
                "response": secret,
                "error": secret,
                "metadata": {"session_id": secret},
                "events": [],
            }]
        }
        payload = json.dumps(legacy, ensure_ascii=False)
        path.write_text(payload, encoding="utf-8")
        Path(f"{path}.bak").write_text(payload, encoding="utf-8")

        controller = ExecutionController(path, incident_manager=None)

        self.assertTrue(controller.records[0]["privacy_safe"])
        self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertNotIn(secret, Path(f"{path}.bak").read_text(encoding="utf-8"))
        self.assertNotIn("label", controller.records[0])
        self.assertNotIn("response", controller.records[0])

    def test_running_and_pending_diagnostics_do_not_expose_command_text(self):
        path = Path(self.temp_dir.name) / "live-diagnostics.json"
        secret = r"C:\Users\Alice\Private\live.xlsx 고객 원문"
        controller = ExecutionController(path, incident_manager=None)
        execution_id = controller.begin(secret, {"session_id": secret})
        current = json.dumps(
            controller.diagnostics()["current"], ensure_ascii=False
        )
        self.assertNotIn(secret, current)
        controller.pause_for_confirmation("confirm-1")
        pending = json.dumps(
            controller.diagnostics()["pending"], ensure_ascii=False
        )
        self.assertNotIn(secret, pending)
        self.assertTrue(controller.resume(execution_id))
        controller.finish(False, error=secret, extra={"failed_step": secret})
        persisted = path.read_text(encoding="utf-8")
        self.assertNotIn(secret, persisted)


if __name__ == "__main__":
    unittest.main()
