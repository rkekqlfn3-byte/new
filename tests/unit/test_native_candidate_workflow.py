"""Stage 11 tests for native candidate prioritization and review workflow."""

import json
import os
import tempfile
import unittest

from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)


def learning(intent="EXPORT_SUMMARY", slots=None):
    return {
        "intent": intent,
        "utterances": [
            "민감한 원문 명령",
            "{file} 문서를 {format} 형식으로 처리해줘",
        ],
        "slots": slots or [
            {
                "name": "file",
                "type": "path",
                "value": r"C:\private\customer.xlsx",
                "required": True,
            },
            {
                "name": "format",
                "type": "value",
                "value": "pdf",
                "required": True,
            },
        ],
    }


class NativeCandidateWorkflowTests(unittest.TestCase):
    def make_manager(self, root, **kwargs):
        return NativeActionCandidateManager(
            os.path.join(root, "native_action_candidates.json"),
            existing_native_actions=kwargs.pop("existing_native_actions", {}),
            **kwargs,
        )

    def test_actual_python_route_is_stored_and_other_routes_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            ignored = manager.record_success(
                "Excel", learning(), execution_id="plan-1",
                selected_route="action_plan",
            )
            record = manager.record_success(
                "Excel", learning(), execution_id="python-1",
                selected_route="python",
            )

        self.assertIsNone(ignored)
        self.assertEqual("python", record["selected_route"])
        self.assertEqual(1, record["process_success_count"])

    def test_test_observations_and_duplicate_execution_ids_do_not_promote(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            for index in range(3):
                manager.record_success(
                    "Excel", learning(), execution_id=f"test-{index}",
                    observation_kind="test", verified=True, user_confirmed=True,
                )
            first = manager.record_success(
                "Excel", learning(), execution_id="real-1", user_confirmed=True
            )
            duplicate = manager.record_success(
                "Excel", learning(), execution_id="real-1", verified=True
            )

        self.assertEqual(3, duplicate["test_process_success_count"])
        self.assertEqual(1, first["process_success_count"])
        self.assertEqual(1, duplicate["process_success_count"])
        self.assertEqual("observing", duplicate["status"])

    def test_general_candidate_readiness_uses_explicit_rule(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            latest = None
            for index in range(3):
                latest = manager.record_success(
                    "Excel", learning(), execution_id=f"general-{index}",
                    user_confirmed=index == 0,
                )

        self.assertTrue(latest["general_candidate"])
        self.assertFalse(latest["strong_candidate"])
        self.assertEqual("ready_for_review", latest["status"])

    def test_strong_candidate_requires_three_verified_successes_on_two_documents(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            manager.record_success(
                "Excel", learning(), execution_id="strong-1", verified=True,
                document_id=r"C:\private\first.xlsx",
            )
            manager.record_success(
                "Excel", learning(), execution_id="strong-2", verified=True,
                document_id=r"C:\private\second.xlsx",
            )
            latest = manager.record_success(
                "Excel", learning(), execution_id="strong-3", verified=True,
                document_id=r"C:\private\second.xlsx",
            )

        self.assertEqual(2, latest["distinct_document_count"])
        self.assertTrue(latest["strong_candidate"])
        self.assertEqual("ready_for_review", latest["status"])
        self.assertEqual("strongly_recommended", latest["native_recommendation"])

    def test_review_status_flow_generates_spec_without_code(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            record = None
            for index in range(3):
                record = manager.record_success(
                    "Excel", learning(), execution_id=f"review-{index}",
                    user_confirmed=index == 0,
                )
            accepted = manager.set_status(
                record["candidate_id"], "accepted", reason="user_approved"
            )
            spec = manager.get_implementation_spec(record["candidate_id"])
            implemented = manager.set_status(
                record["candidate_id"], "implemented",
                reason="implementation_completed",
            )

        self.assertEqual("accepted", accepted["status"])
        self.assertEqual("export_summary", spec["task_name"])
        self.assertEqual("ExcelAdapter", spec["recommended_implementation_location"])
        self.assertFalse(spec["code_generated"])
        self.assertNotIn("code", spec)
        self.assertEqual("implemented", implemented["status"])

    def test_reject_as_macro_sufficient_and_reopen(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            record = manager.record_success(
                "Excel", learning("EXPORT_DETAIL"), execution_id="reject-1"
            )
            rejected = manager.set_status(
                record["candidate_id"], "rejected", reason="macro_sufficient"
            )
            reopened = manager.set_status(
                record["candidate_id"], "observing", reason="review_reopened"
            )

        self.assertEqual("sufficient", rejected["macro_sufficiency"])
        self.assertEqual("observing", reopened["status"])
        self.assertEqual("undetermined", reopened["macro_sufficiency"])

    def test_existing_native_action_is_merged_automatically(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(
                root, existing_native_actions={"excel": {"write_cell"}}
            )
            record = manager.record_success(
                "Excel", learning("WRITE_CELL"), execution_id="native-1"
            )

        self.assertEqual("merged", record["status"])
        self.assertEqual("already_native", record["native_recommendation"])
        self.assertEqual("ExcelAdapter.write_cell", record["merged_with"])

    def test_slot_only_variants_merge_into_one_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.make_manager(root)
            manager.record_success(
                "Excel",
                learning(slots=[{"name": "file", "type": "path"}]),
                execution_id="slot-1",
            )
            latest = manager.record_success(
                "Excel",
                learning(slots=[
                    {"name": "file", "type": "path"},
                    {"name": "format", "type": "value"},
                ]),
                execution_id="slot-2",
            )
            listed = manager.list_candidates()

        self.assertEqual(1, len(listed))
        self.assertEqual(2, latest["process_success_count"])
        self.assertEqual({"file", "format"}, {
            item["name"] for item in latest["required_slots"]
        })

    def test_sensitive_values_and_document_identity_are_never_stored(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "native_action_candidates.json")
            manager = self.make_manager(root)
            record = manager.record_success(
                "Excel", learning(),
                description=r"C:\private\customer.xlsx user@example.com token=abc123",
                execution_id="privacy-1", user_confirmed=True, verified=True,
                document_id=r"C:\private\customer.xlsx",
            )
            with open(path, "r", encoding="utf-8") as source:
                raw = source.read()

        self.assertNotIn("customer.xlsx", raw)
        self.assertNotIn("user@example.com", raw)
        self.assertNotIn("abc123", raw)
        self.assertNotIn("민감한 원문", raw)
        self.assertNotIn('"pdf"', raw)
        self.assertEqual(1, record["distinct_document_count"])
        self.assertNotIn("document_fingerprints", record)

    def test_schema_two_status_and_detail_fields_migrate_to_stage11(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "native_action_candidates.json")
            old_signature = "a" * 64
            with open(path, "w", encoding="utf-8") as output:
                json.dump({
                    "schema_version": 2,
                    "candidates": {
                        old_signature: {
                            "candidate_id": old_signature[:16],
                            "signature": old_signature,
                            "app": "Excel",
                            "intent": "EXPORT_SUMMARY",
                            "slot_schema": [{
                                "name": "file", "type": "path", "required": True,
                            }],
                            "status": "dismissed",
                            "process_success_count": 2,
                        }
                    },
                }, output)
            manager = self.make_manager(root)
            record = manager.list_candidates(include_dismissed=True)[0]
            with open(path, "r", encoding="utf-8") as source:
                stored = json.load(source)

        self.assertEqual(3, stored["schema_version"])
        self.assertEqual("rejected", record["status"])
        self.assertEqual("EXPORT_SUMMARY", record["normalized_intent"])
        self.assertEqual("excel", record["target_app"])
        self.assertEqual("python", record["selected_route"])
        self.assertIn("risk_level", record)
        self.assertIn("verification_hint", record)
        self.assertIn("rollback_required", record)


if __name__ == "__main__":
    unittest.main()
