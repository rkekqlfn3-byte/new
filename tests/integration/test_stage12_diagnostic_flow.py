import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.api import command_api
from engine.diagnostics import DiagnosticIncidentManager
from engine.execution_result import failure_result
from engine.execution_runtime import ExecutionController
from engine.parser import CommandParser


class Stage12DiagnosticFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.incident_path = self.root / "diagnostic_incidents.json"
        self.manager = DiagnosticIncidentManager(
            self.incident_path,
            environment_provider=lambda: {
                "app_version": "integration-test",
                "applications": {
                    "excel": {"installed": True, "version": "16.0"},
                    "word": {"installed": True, "version": "16.0"},
                    "powerpoint": {"installed": True, "version": "16.0"},
                    "hwp": {"installed": False, "version": None},
                },
            },
        )
        self.controller = ExecutionController(
            self.root / "execution_diagnostics.json",
            incident_manager=self.manager,
        )
        self.parser = CommandParser()
        self.parser.execution_controller = self.controller
        self.parser.action_executor.controller = self.controller
        self.parser.macro_runner.controller = self.controller

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_command_failure_creates_report_and_natural_diagnosis(self):
        secret_command = r"C:\Users\Alice\Private\payroll.xlsx 고객 값을 바꿔줘"
        failure = failure_result(
            "C:\\Users\\Alice\\Private\\payroll.xlsx 처리 중 실패",
            action="business_workflow",
            error_type="execution_error",
            failed_step="create_powerpoint_summary",
            retryable=True,
            data={
                "operation": "create_business_workflow",
                "app_type": "powerpoint",
                "document_name": "payroll.xlsx",
            },
        )
        with (
            patch.object(command_api, "parser", self.parser),
            patch.object(self.parser, "_parse_and_execute_core", return_value=failure),
        ):
            result = command_api.parse_command(
                secret_command,
                mode="edit",
                edit_context={
                    "edit_session_id": "private-session",
                    "document_fingerprint": "private-document-fingerprint",
                    "context_fingerprint": "E" * 64,
                },
            )

        self.assertFalse(result["success"])
        self.assertEqual(
            "implementation_bug",
            result["data"]["failure_triage"]["category"],
        )
        self.assertEqual("developer", result["data"]["failure_triage"]["owner"])
        self.assertIn("자동 진단(제안)", result["display_message"])
        self.assertEqual(
            "C:\\Users\\Alice\\Private\\payroll.xlsx 처리 중 실패",
            result["message"],
        )
        incidents = self.manager.list_incidents()
        self.assertEqual(1, len(incidents))
        incident = incidents[0]
        self.assertEqual(
            "create_powerpoint_summary",
            incident["interpreted"]["failed_step"],
        )
        self.assertEqual(
            incident["incident_id"],
            self.controller.records[-1]["diagnostic_incident_id"],
        )
        serialized = self.incident_path.read_text(encoding="utf-8")
        execution_serialized = (
            self.root / "execution_diagnostics.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn(secret_command, serialized)
        self.assertNotIn("private-session", serialized)
        self.assertNotIn("private-document-fingerprint", serialized)
        self.assertNotIn(secret_command, execution_serialized)
        self.assertNotIn("private-session", execution_serialized)

        with patch.object(command_api, "parser", self.parser):
            diagnosed = command_api.parse_command("최근 오류 진단해줘")
            api_report = command_api.get_self_diagnostic_report()
            proposal = command_api.get_remediation_proposal(
                incident["incident_id"]
            )
            issues = command_api.get_developer_issues()
        self.assertTrue(diagnosed["success"])
        self.assertTrue(diagnosed["verified"])
        self.assertIn("create_powerpoint_summary", diagnosed["message"])
        self.assertTrue(api_report["success"])
        self.assertEqual(
            incident["incident_id"], api_report["report"]["incident_id"]
        )
        self.assertEqual("proposal_only", proposal["proposal"]["status"])
        self.assertFalse(proposal["proposal"]["automatic_execution_allowed"])
        self.assertTrue(issues["success"])
        self.assertEqual(1, len(issues["issues"]))
        self.assertEqual("implementation_bug", issues["issues"][0]["category"])
        self.assertEqual(1, len(self.manager.list_incidents()))

    def test_empty_diagnosis_does_not_create_a_failure_incident(self):
        with patch.object(command_api, "parser", self.parser):
            result = command_api.parse_command("자가진단")
            report = command_api.get_self_diagnostic_report()
        self.assertFalse(result["success"])
        self.assertEqual("target_not_found", result["error_type"])
        self.assertFalse(report["success"])
        self.assertEqual([], self.manager.list_incidents())

    def test_status_and_health_apis_are_read_only(self):
        incident = self.manager.record_execution_failure({
            "execution_id": "failure-1",
            "label": "test",
            "success": False,
            "status": "failed",
            "error_type": "verification_error",
            "verified": False,
            "result": {
                "success": False,
                "action": "edit_action",
                "status": "failed",
                "error_type": "verification_error",
                "verified": False,
                "data": {"operation": "format", "app_type": "excel"},
            },
        })
        with patch.object(command_api, "parser", self.parser):
            health = command_api.get_diagnostic_health_summary()
            changed = command_api.set_diagnostic_incident_status(
                incident["incident_id"], "acknowledged"
            )
        self.assertTrue(health["success"])
        self.assertEqual(1, health["health"]["incident_groups"])
        self.assertFalse(health["health"]["automatic_code_change"])
        self.assertEqual(
            {"implementation_bug": 1}, health["health"]["triage_categories"]
        )
        self.assertEqual({"developer": 1}, health["health"]["owners"])
        self.assertEqual(1, health["health"]["developer_issue_count"])
        self.assertEqual("acknowledged", changed["incident"]["status"])

    def test_classifier_failure_does_not_replace_the_original_command_result(self):
        class BrokenClassifier:
            def classify(self, _record):
                raise RuntimeError("classifier unavailable")

        manager = DiagnosticIncidentManager(
            self.incident_path,
            environment_provider=lambda: {},
            classifier=BrokenClassifier(),
        )
        controller = ExecutionController(
            self.root / "isolated-execution.json", incident_manager=manager
        )
        self.parser.execution_controller = controller
        self.parser.action_executor.controller = controller
        self.parser.macro_runner.controller = controller
        failure = failure_result(
            "원래 실행 오류",
            action="edit_action",
            error_type="execution_error",
            data={"operation": "format", "app_type": "excel"},
        )
        with (
            patch.object(command_api, "parser", self.parser),
            patch.object(self.parser, "_parse_and_execute_core", return_value=failure),
        ):
            result = command_api.parse_command("테스트 명령")

        self.assertEqual("원래 실행 오류", result["message"])
        self.assertEqual("unknown", result["data"]["failure_triage"]["category"])
        self.assertEqual(1, len(manager.list_incidents()))

    def test_diagnostic_storage_failure_does_not_replace_command_result(self):
        failure = failure_result(
            "저장소와 무관한 원래 오류",
            action="edit_action",
            error_type="verification_error",
            data={"operation": "format", "app_type": "excel"},
        )
        with (
            patch.object(command_api, "parser", self.parser),
            patch.object(self.parser, "_parse_and_execute_core", return_value=failure),
            patch.object(self.manager, "_save_locked", side_effect=OSError("disk full")),
        ):
            result = command_api.parse_command("테스트 명령")

        self.assertEqual("저장소와 무관한 원래 오류", result["message"])
        self.assertNotIn("display_message", result)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
