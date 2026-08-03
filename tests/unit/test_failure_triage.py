import json
import tempfile
import unittest
from pathlib import Path

from engine.diagnostics.failure_triage import (
    DeterministicFailureClassifier,
    DeveloperIssueRegistry,
    FailureCategory,
    FailureOwner,
    normalize_triage,
)


class FailureTriageTests(unittest.TestCase):
    def setUp(self):
        self.classifier = DeterministicFailureClassifier()

    @staticmethod
    def record(**changes):
        record = {
            "success": False,
            "status": "failed",
            "error_type": "unknown",
            "retryable": False,
            "events": [],
            "result": {
                "success": False,
                "message": "작업 실패",
                "action": "command",
                "status": "failed",
                "error_type": "unknown",
                "retryable": False,
                "verified": False,
                "data": {},
            },
        }
        record.update(changes)
        return record

    def classify(self, **changes):
        return self.classifier.classify(self.record(**changes)).to_dict()

    def test_runtime_recoverable_target_can_only_recommend_retry(self):
        triage = self.classify(
            error_type="target_not_found",
            retryable=True,
            result={
                "success": False,
                "message": "대상을 찾을 수 없습니다.",
                "action": "command",
                "status": "not_found",
                "error_type": "target_not_found",
                "retryable": True,
                "data": {},
            },
        )
        self.assertEqual(FailureCategory.RUNTIME_RECOVERABLE.value, triage["category"])
        self.assertEqual(FailureOwner.JARVIS.value, triage["owner"])
        self.assertTrue(triage["retry_allowed"])
        self.assertEqual(0, triage["retry_count"])

    def test_needs_input_requires_an_explicit_choice(self):
        triage = self.classify(result={
            "success": False,
            "message": "같은 이름의 대상이 여러 개입니다. 하나를 선택해주세요.",
            "action": "command",
            "status": "failed",
            "error_type": "target_not_found",
            "data": {"triage_hints": {"multiple_targets": True}},
        })
        self.assertEqual(FailureCategory.NEEDS_INPUT.value, triage["category"])
        self.assertEqual(FailureOwner.USER.value, triage["owner"])
        self.assertFalse(triage["retry_allowed"])

    def test_exhausted_app_discovery_moves_ownership_to_user_input(self):
        triage = self.classify(
            error_type="target_not_found",
            result={
                "success": False,
                "message": "Windows 앱 목록도 다시 확인했지만 찾지 못했습니다.",
                "action": "open_app",
                "status": "failed",
                "error_type": "target_not_found",
                "retryable": False,
                "data": {
                    "retry_count": 1,
                    "automatic_recovery": {
                        "schema_version": 1,
                        "attempted": True,
                        "strategy": "bounded_app_rediscovery",
                        "phase": "pre_execution",
                        "target_signature": "A" * 64,
                        "execution_started": False,
                        "target_unchanged": True,
                        "target_resolved": False,
                        "retry_count": 1,
                        "retry_limit": 1,
                        "outcome": "not_found",
                    },
                },
            },
        )
        self.assertEqual(FailureCategory.NEEDS_INPUT.value, triage["category"])
        self.assertEqual(FailureOwner.USER.value, triage["owner"])
        self.assertEqual("ask_for_app_name_or_location", triage["next_action"])
        self.assertEqual(1, triage["retry_count"])
        self.assertIn("TARGET_DISCOVERY_EXHAUSTED", triage["evidence_codes"])
        self.assertIn("SAFE_PREEXECUTION_RECOVERY", triage["evidence_codes"])
        self.assertFalse(triage["retry_allowed"])

    def test_changed_recovery_target_requires_user_input_without_execution(self):
        triage = self.classify(result={
            "success": False,
            "message": "재탐색 전후의 요청 대상이 달라 자동 실행을 멈췄습니다.",
            "action": "open_app",
            "status": "failed",
            "error_type": "target_not_found",
            "retryable": False,
            "data": {
                "retry_count": 1,
                "triage_hints": {"recovery_target_changed": True},
                "automatic_recovery": {
                    "schema_version": 1,
                    "attempted": True,
                    "strategy": "bounded_app_rediscovery",
                    "phase": "pre_execution",
                    "target_signature": "B" * 64,
                    "execution_started": False,
                    "target_unchanged": False,
                    "target_resolved": False,
                    "retry_count": 1,
                    "retry_limit": 1,
                    "outcome": "target_changed",
                },
            },
        })
        self.assertEqual(FailureCategory.NEEDS_INPUT.value, triage["category"])
        self.assertEqual(FailureOwner.USER.value, triage["owner"])
        self.assertFalse(triage["execution_started"])
        self.assertIn("RECOVERY_TARGET_CHANGED", triage["evidence_codes"])
        self.assertIn("SAFE_PREEXECUTION_RECOVERY", triage["evidence_codes"])
        self.assertFalse(triage["retry_allowed"])

    def test_recovery_after_execution_is_a_developer_owned_safety_failure(self):
        triage = self.classify(result={
            "success": False,
            "message": "실행 시작 뒤 자동 재시도를 차단했습니다.",
            "action": "open_app",
            "status": "failed",
            "error_type": "execution_error",
            "retryable": True,
            "data": {
                "retry_count": 0,
                "automatic_recovery": {
                    "schema_version": 1,
                    "attempted": False,
                    "strategy": "bounded_app_rediscovery",
                    "phase": "pre_execution",
                    "target_signature": "C" * 64,
                    "execution_started": True,
                    "target_unchanged": True,
                    "target_resolved": False,
                    "retry_count": 0,
                    "retry_limit": 1,
                    "outcome": "blocked_after_execution",
                },
            },
        })
        self.assertEqual(FailureCategory.IMPLEMENTATION_BUG.value, triage["category"])
        self.assertEqual(FailureOwner.DEVELOPER.value, triage["owner"])
        self.assertTrue(triage["execution_started"])
        self.assertIn(
            "RECOVERY_AFTER_EXECUTION_BLOCKED", triage["evidence_codes"]
        )
        self.assertFalse(triage["retry_allowed"])

    def test_missing_capability_is_a_local_developer_item(self):
        triage = self.classify(result={
            "success": False,
            "message": "operation_not_registered",
            "action": "edit_action",
            "status": "failed",
            "error_type": "validation_error",
            "data": {
                "operation": "new_operation",
                "triage_hints": {"capability_exists": False},
            },
        })
        self.assertEqual(FailureCategory.MISSING_CAPABILITY.value, triage["category"])
        self.assertEqual(FailureOwner.DEVELOPER.value, triage["owner"])

    def test_verification_failure_is_an_implementation_bug(self):
        triage = self.classify(
            error_type="verification_error",
            result={
                "success": False,
                "message": "사후조건 불일치",
                "action": "edit_action",
                "status": "failed",
                "error_type": "verification_error",
                "data": {"operation": "format"},
            },
        )
        self.assertEqual(FailureCategory.IMPLEMENTATION_BUG.value, triage["category"])
        self.assertEqual(False, triage["verification_succeeded"])
        self.assertFalse(triage["retry_allowed"])

    def test_environment_block_takes_precedence_over_execution_error(self):
        triage = self.classify(
            error="문서가 읽기 전용입니다.",
            error_type="execution_error",
            result={
                "success": False,
                "message": "문서가 읽기 전용입니다.",
                "action": "edit_action",
                "status": "failed",
                "error_type": "execution_error",
                "data": {"operation": "write"},
            },
        )
        self.assertEqual(FailureCategory.ENVIRONMENT_BLOCKED.value, triage["category"])
        self.assertEqual(FailureOwner.ENVIRONMENT.value, triage["owner"])

    def test_typed_environment_error_does_not_require_message_guessing(self):
        triage = self.classify(
            error_type="environment_error",
            result={
                "success": False,
                "message": "외부 상태 때문에 완료하지 못했습니다.",
                "action": "edit_action",
                "status": "failed",
                "error_type": "environment_error",
                "data": {"operation": "write"},
            },
        )
        self.assertEqual(FailureCategory.ENVIRONMENT_BLOCKED.value, triage["category"])

    def test_policy_block_never_allows_retry(self):
        triage = self.classify(result={
            "success": False,
            "message": "보안상 허용하지 않는 직접 명령 실행입니다.",
            "action": "shell",
            "status": "blocked",
            "error_type": "validation_error",
            "retryable": True,
            "data": {"triage_hints": {"policy_denied": True}},
        })
        self.assertEqual(FailureCategory.POLICY_BLOCKED.value, triage["category"])
        self.assertEqual(FailureOwner.POLICY.value, triage["owner"])
        self.assertFalse(triage["retry_allowed"])

    def test_preference_mismatch_requires_verified_success_and_explicit_rejection(self):
        record = self.record(
            success=True,
            status="success",
            verified=True,
            result={
                "success": True,
                "message": "완료",
                "action": "edit_action",
                "status": "success",
                "verified": True,
                "data": {
                    "triage_hints": {"user_rejected_verified_result": True}
                },
            },
        )
        triage = self.classifier.classify(record).to_dict()
        self.assertEqual(FailureCategory.PREFERENCE_MISMATCH.value, triage["category"])
        self.assertEqual(FailureOwner.USER.value, triage["owner"])

    def test_generic_validation_failure_is_not_mislabeled_as_policy(self):
        triage = self.classify(error_type="validation_error")
        self.assertEqual(FailureCategory.UNKNOWN.value, triage["category"])
        self.assertEqual(FailureOwner.UNKNOWN.value, triage["owner"])

    def test_normalization_replaces_all_free_form_messages(self):
        secret = r"C:\Users\Alice\Private\payroll.xlsx alice@example.com"
        triage = normalize_triage({
            "category": "implementation_bug",
            "owner": "developer",
            "evidence_codes": ["POSTCONDITION_FAILED"],
            "user_message": secret,
            "developer_summary": secret,
        })
        self.assertNotIn(secret, json.dumps(triage, ensure_ascii=False))

    def test_registry_groups_only_developer_owned_items_without_raw_content(self):
        secret = r"C:\Users\Alice\Private\payroll.xlsx alice@example.com"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "developer_issues.json"
            registry = DeveloperIssueRegistry(path)
            triage = self.classify(
                error_type="verification_error",
                result={
                    "success": False,
                    "message": secret,
                    "action": "edit_action",
                    "status": "failed",
                    "error_type": "verification_error",
                    "data": {"operation": "format"},
                },
            )
            first = registry.record(
                triage,
                incident={"incident_id": "a" * 32},
                interpreted={
                    "app_type": "excel",
                    "operation": "format",
                    "action": "edit_action",
                    "document_extension": ".xlsx",
                    "error_type": "verification_error",
                },
                error_signature=secret,
                request_hash=secret,
            )
            second = registry.record(
                triage,
                incident={"incident_id": "b" * 32},
                interpreted={
                    "app_type": "excel",
                    "operation": "format",
                    "action": "edit_action",
                    "document_extension": ".xlsx",
                    "error_type": "verification_error",
                },
                error_signature="different exception text",
                request_hash="different command",
            )
            runtime = self.classify(error_type="target_not_found", retryable=True)
            self.assertIsNone(registry.record(
                runtime,
                incident={"incident_id": "c" * 32},
                interpreted={},
                error_signature=secret,
                request_hash=secret,
            ))
            self.assertEqual(first["issue_id"], second["issue_id"])
            self.assertEqual(2, second["frequency"])
            self.assertEqual(1, registry.count())
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))
            self.assertFalse(second["external_issue_created"])
            self.assertFalse(second["automatic_code_change"])
            reloaded = DeveloperIssueRegistry(path).list_issues()
            self.assertEqual(2, reloaded[0]["frequency"])
            self.assertIn("T", reloaded[0]["last_seen_at"])


if __name__ == "__main__":
    unittest.main()
