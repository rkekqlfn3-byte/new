import json
import os
import tempfile
import unittest

from engine.managers.dict_manager import DictionaryManager
from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)
from engine.parser import CommandParser


def sample_learning(intent="EXPORT_SUMMARY"):
    return {
        "intent": intent,
        "argument_mode": "json",
        "verbs": ["내보내"],
        "nouns": [],
        "utterances": [
            "민감 고객 보고서를 내보내줘",
            "{file} 보고서를 {format} 형식으로 내보내줘",
        ],
        "slots": [
            {
                "name": "file",
                "type": "path",
                "value": r"C:\private folder\customer.xlsx",
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


class NativeActionCandidateManagerTests(unittest.TestCase):
    def make_manager(self, temp_dir):
        return NativeActionCandidateManager(
            os.path.join(temp_dir, "native_candidates.json")
        )

    def test_three_distinct_successes_with_confirmation_become_review_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(temp_dir)
            learning = sample_learning()
            first = manager.record_success(
                "Excel", learning, "보고서 내보내기",
                source="confirmed_dynamic_code", execution_id="one",
                user_confirmed=True,
            )
            duplicate = manager.record_success(
                "Excel", learning, "보고서 내보내기",
                execution_id="one",
            )
            second = manager.record_success(
                "Excel", learning, "보고서 내보내기", execution_id="two"
            )
            third = manager.record_success(
                "Excel", learning, "보고서 내보내기", execution_id="three"
            )

            self.assertEqual(1, first["process_success_count"])
            self.assertEqual(1, first["user_confirmed_count"])
            self.assertEqual(1, duplicate["process_success_count"])
            self.assertEqual("observing", second["status"])
            self.assertEqual("ready_for_review", third["status"])
            public = manager.list_candidates()
            self.assertEqual(1, len(public))
            self.assertFalse(public[0]["automatic_execution"])
            self.assertNotIn("signature", public[0])
            self.assertNotIn("evidence_ids", public[0])

    def test_failure_resets_consecutive_successes_and_clears_ready_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(temp_dir)
            learning = sample_learning()
            for index in range(3):
                manager.record_success(
                    "Excel", learning, execution_id=f"success-{index}",
                    user_confirmed=index == 0,
                )
            failed = manager.record_failure(
                "Excel", learning, execution_id="failure-1"
            )
            self.assertEqual("observing", failed["status"])
            self.assertEqual(0, failed["consecutive_process_success"])
            self.assertEqual(0, failed["consecutive_verified_success"])
            self.assertEqual(1, failed["failure_count"])
            self.assertGreater(failed["failure_count"] / 4, 0.20)

            for index in range(3, 6):
                latest = manager.record_success(
                    "Excel", learning, execution_id=f"success-{index}"
                )
            self.assertEqual("ready_for_review", latest["status"])

    def test_unconfirmed_generic_and_risky_intents_are_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(temp_dir)
            learning = sample_learning()
            for index in range(4):
                result = manager.record_success(
                    "Excel", learning, execution_id=f"plain-{index}"
                )
            self.assertEqual("observing", result["status"])
            self.assertEqual([], manager.list_candidates(include_observing=False))
            self.assertIsNone(
                manager.record_success(
                    "system", sample_learning("LEARNED_ACTION"),
                    execution_id="generic", user_confirmed=True,
                )
            )
            self.assertIsNone(
                manager.record_success(
                    "system", sample_learning("DELETE_FILES"),
                    execution_id="danger", user_confirmed=True,
                )
            )

    def test_storage_omits_code_slot_values_literal_commands_and_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "native_candidates.json")
            manager = NativeActionCandidateManager(path)
            manager.record_success(
                "Excel",
                sample_learning(),
                r"C:\private folder\customer.xlsx 내보내기",
                execution_id="private-1",
                user_confirmed=True,
            )
            with open(path, "r", encoding="utf-8") as source:
                stored = json.load(source)
            encoded = json.dumps(stored, ensure_ascii=False)
            self.assertNotIn("customer.xlsx", encoded)
            self.assertNotIn("민감 고객", encoded)
            self.assertNotIn('"pdf"', encoded)
            self.assertNotIn("import ", encoded)
            self.assertIn("{file}", encoded)
            self.assertIn('"type": "path"', encoded)

    def test_legacy_dismiss_maps_to_rejected_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "native_candidates.json")
            manager = NativeActionCandidateManager(path)
            learning = sample_learning()
            record = manager.record_success(
                "Excel", learning, execution_id="one", user_confirmed=True
            )
            dismissed = manager.set_status(record["candidate_id"], "dismissed")
            self.assertEqual("rejected", dismissed["status"])
            self.assertEqual([], manager.list_candidates())

            reloaded = NativeActionCandidateManager(path)
            hidden = reloaded.list_candidates(include_dismissed=True)
            self.assertEqual("rejected", hidden[0]["status"])
            observed = reloaded.set_status(record["candidate_id"], "observing")
            self.assertEqual(1, observed["consecutive_process_success"])
            self.assertEqual(0, observed["consecutive_verified_success"])

    def test_process_verified_and_user_confirmation_metrics_are_independent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(temp_dir)
            learning = sample_learning()
            manager.record_success(
                "Excel", learning, execution_id="one",
                user_confirmed=True, verified=True,
            )
            manager.record_success(
                "Excel", learning, execution_id="two", verified=True
            )
            third = manager.record_success(
                "Excel", learning, execution_id="three", verified=False
            )
            self.assertEqual(3, third["process_success_count"])
            self.assertEqual(2, third["verified_success_count"])
            self.assertEqual(1, third["user_confirmed_count"])
            self.assertEqual(3, third["consecutive_process_success"])
            self.assertEqual(0, third["consecutive_verified_success"])
            self.assertFalse(manager.list_candidates()[0]["high_verification"])

            fourth = manager.record_success(
                "Excel", learning, execution_id="four", verified=True
            )
            self.assertEqual(3, fourth["verified_success_count"])
            self.assertTrue(manager.list_candidates()[0]["high_verification"])

    def test_test_observations_are_separate_and_never_promote(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(temp_dir)
            learning = sample_learning()
            for index in range(3):
                record = manager.record_success(
                    "Excel", learning, execution_id=f"test-{index}",
                    user_confirmed=True, verified=True,
                    observation_kind="test",
                )
            self.assertEqual(0, record["process_success_count"])
            self.assertEqual(0, record["verified_success_count"])
            self.assertEqual(0, record["user_confirmed_count"])
            self.assertEqual(3, record["test_process_success_count"])
            self.assertEqual(3, record["test_verified_success_count"])
            self.assertEqual("observing", record["status"])

    def test_schema_one_candidates_migrate_without_inventing_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "native_candidates.json")
            with open(path, "w", encoding="utf-8") as target:
                json.dump({
                    "schema_version": 1,
                    "candidates": {
                        "signature": {
                            "candidate_id": "signature",
                            "signature": "signature",
                            "status": "observing",
                            "success_count": 5,
                            "confirmed_success_count": 2,
                            "consecutive_successes": 3,
                            "failure_count": 1,
                        }
                    },
                }, target)

            manager = NativeActionCandidateManager(path)
            migrated = manager._candidates["signature"]
            self.assertEqual(5, migrated["process_success_count"])
            self.assertEqual(2, migrated["user_confirmed_count"])
            self.assertEqual(3, migrated["consecutive_process_success"])
            self.assertEqual(0, migrated["verified_success_count"])
            self.assertEqual(0, migrated["consecutive_verified_success"])
            self.assertNotIn("success_count", migrated)
            with open(path, "r", encoding="utf-8") as source:
                self.assertEqual(3, json.load(source)["schema_version"])


class NativeActionCandidateParserIntegrationTests(unittest.TestCase):
    def test_confirmed_dynamic_learning_and_two_reuses_create_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate_manager = NativeActionCandidateManager(
                os.path.join(temp_dir, "native_candidates.json")
            )
            parser = CommandParser(
                native_action_candidate_manager=candidate_manager
            )
            parser.dict_mgr = DictionaryManager(
                os.path.join(temp_dir, "dictionaries.json")
            )
            parser.pending_macros = [{
                "code": "print('ok')",
                "plan": [],
                "app": "Excel",
                "name": "export_summary",
                "desc": "요약 보고서 내보내기",
                "steps": [],
                "target": "",
                "utterances": [],
                "learning": sample_learning(),
                "verification_status": "confirmation_required",
                "verification": [],
                "candidate_execution_id": "original-run",
            }]

            parser.approve_pending_learning()
            learned = parser.dict_mgr.learned_macros["Excel"]["export_summary"]
            self.assertEqual("user_confirmed", learned["verification_status"])
            self.assertTrue(learned["native_candidate_signature"])
            self.assertEqual(
                1,
                candidate_manager.list_candidates()[0]["process_success_count"],
            )

            parser._record_native_candidate_success(
                "Excel", learned,
                execution_result={
                    "verified": True, "verification_status": "verified"
                },
            )
            parser._record_native_candidate_success(
                "Excel", learned,
                execution_result={
                    "verified": True, "verification_status": "verified"
                },
            )
            candidate = candidate_manager.list_candidates()[0]
            self.assertEqual(3, candidate["process_success_count"])
            self.assertEqual(2, candidate["verified_success_count"])
            self.assertEqual(1, candidate["user_confirmed_count"])
            self.assertEqual("ready_for_review", candidate["status"])

    def test_default_candidate_store_follows_replaced_dictionary_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stale_path = os.path.join(temp_dir, "stale", "native.json")
            dictionary_path = os.path.join(
                temp_dir, "isolated", "dictionaries.json"
            )
            expected_path = os.path.join(
                temp_dir, "isolated", "native_action_candidates.json"
            )
            parser = CommandParser(
                native_action_candidate_manager=(
                    NativeActionCandidateManager(stale_path)
                )
            )
            parser._native_candidate_manager_injected = False
            parser.dict_mgr = DictionaryManager(dictionary_path)
            parser.pending_macros = [{
                "code": "print('ok')",
                "plan": [],
                "app": "Excel",
                "name": "isolated_export",
                "desc": "격리 저장 테스트",
                "steps": [],
                "target": "",
                "learning": sample_learning("ISOLATED_EXPORT"),
                "verification_status": "confirmation_required",
                "verification": [],
                "candidate_execution_id": "isolated-run",
            }]

            parser.approve_pending_learning()

            self.assertTrue(os.path.isfile(expected_path))
            self.assertFalse(os.path.exists(stale_path))


if __name__ == "__main__":
    unittest.main()
