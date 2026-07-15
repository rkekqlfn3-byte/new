"""Unit tests for extracted skill learning and candidate recording services."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from engine.managers.dict_manager import DictionaryManager
from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)
from engine.parser import CommandParser
from engine.skills import CandidateRecordingService, SkillLearningService


def learning_metadata():
    return {
        "intent": "SHOW_TEST_VALUE",
        "argument_mode": "json",
        "verbs": ["보여줘"],
        "nouns": [],
        "utterances": ["{value} 값을 보여줘"],
        "slots": [
            {"name": "value", "type": "value", "required": True}
        ],
    }


def pending_candidate():
    return {
        "app": "시스템",
        "name": "값_표시",
        "desc": "지정한 값 표시",
        "target": "",
        "code": "print('ok')",
        "plan": [],
        "steps": [],
        "learning": learning_metadata(),
        "verification_status": "confirmation_required",
        "verification": [],
    }


class SkillLearningServiceTests(unittest.TestCase):
    def test_stage_and_review_keep_code_private(self):
        owner = SimpleNamespace(
            pending_macros=[],
            dict_mgr=SimpleNamespace(macro_dict={}, learned_macros={}),
        )
        service = SkillLearningService(owner)

        service.stage_candidate(pending_candidate())
        review = service.get_pending_review()

        self.assertTrue(review["pending"])
        self.assertEqual("dynamic_code", review["candidates"][0]["kind"])
        self.assertNotIn("code", review["candidates"][0])
        with self.assertRaisesRegex(ValueError, "학습 후보"):
            service.stage_candidate("invalid")

    def test_parser_approval_compatibility_method_delegates_to_service(self):
        parser = CommandParser()
        parser.skill_learning_service.approve = mock.Mock(return_value="saved")

        result = parser.approve_pending_learning([{"index": 0}])

        self.assertEqual("saved", result)
        parser.skill_learning_service.approve.assert_called_once_with(
            [{"index": 0}]
        )

    def test_route_profile_and_supported_postconditions_survive_approval(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-skill-profile-") as root:
            parser = CommandParser()
            parser.dict_mgr = DictionaryManager(
                os.path.join(root, "dictionaries.json")
            )
            candidate = pending_candidate()
            candidate.update({
                "code": "",
                "plan": [{"action": "wait", "seconds": 0.01}],
                "uia_plan": [{"action": "wait", "seconds": 0.02}],
                "execution_profile": {
                    "primary_route": "action_plan",
                    "fallback_routes": ["uia"],
                    "verification_required": True,
                    "max_fallback_attempts": 1,
                },
                "postconditions": [
                    {
                        "type": "file_exists",
                        "target": {"source": "slot", "name": "value"},
                    },
                    {"type": "ai_invented_check", "target": "ignored"},
                ],
            })
            parser.skill_learning_service.stage_candidate(candidate)

            parser.skill_learning_service.approve()

            saved = parser.dict_mgr.learned_macros["시스템"]["값_표시"]
        self.assertEqual(candidate["uia_plan"], saved["uia_plan"])
        self.assertEqual(
            candidate["execution_profile"], saved["execution_profile"]
        )
        self.assertEqual(1, len(saved["postconditions"]))
        self.assertEqual("file_exists", saved["postconditions"][0]["type"])


class CandidateRecordingServiceTests(unittest.TestCase):
    def test_duplicate_execution_id_is_counted_once(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-candidate-service-") as temp_dir:
            manager = NativeActionCandidateManager(
                os.path.join(temp_dir, "native_action_candidates.json")
            )
            owner = SimpleNamespace(
                _native_candidate_manager_injected=True,
                native_action_candidate_manager=manager,
                dict_mgr=DictionaryManager(
                    os.path.join(temp_dir, "dictionaries.json")
                ),
                _current_execution_id=lambda: "same-execution",
            )
            service = CandidateRecordingService(owner)
            learned = {
                "code": "print('ok')",
                "verification_status": "user_confirmed",
                "learning": learning_metadata(),
                "description": "값 표시",
            }

            first = service.record_success(
                "시스템", learned,
                execution_result={"verified": True},
            )
            duplicate = service.record_success(
                "시스템", learned,
                execution_result={"verified": True},
            )

        self.assertEqual(1, first["process_success_count"])
        self.assertEqual(1, duplicate["process_success_count"])
        self.assertEqual(1, duplicate["verified_success_count"])

    def test_default_store_follows_active_dictionary_directory(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-candidate-service-") as temp_dir:
            stale = os.path.join(temp_dir, "stale", "candidate.json")
            dictionary_path = os.path.join(temp_dir, "active", "dictionaries.json")
            owner = SimpleNamespace(
                _native_candidate_manager_injected=False,
                native_action_candidate_manager=NativeActionCandidateManager(stale),
                dict_mgr=DictionaryManager(dictionary_path),
                _current_execution_id=lambda: "",
            )
            service = CandidateRecordingService(owner)

            selected = service.manager()

        self.assertEqual(
            os.path.normcase(os.path.join(
                temp_dir, "active", "native_action_candidates.json"
            )),
            os.path.normcase(selected.path),
        )


if __name__ == "__main__":
    unittest.main()
