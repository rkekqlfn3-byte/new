import json
import os
import tempfile
import unittest
from unittest import mock

from engine.api import command_api
from engine.app_actions.base import AppActionVerificationError
from engine.app_actions.registry import AppActionRegistry
from engine.decision import PreferenceManager
from engine.execution_runtime import ExecutionController
from engine.parser import CommandParser, EXCEL_FORMAT_PREFERENCE_KEY
from tests.windows.test_excel_core_actions import FakeExcel, adapter_for, seed_sales


class PreferenceManagerTests(unittest.TestCase):
    def test_three_consistent_choices_recommend_and_five_auto_apply(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preferences-") as temp_dir:
            manager = PreferenceManager(os.path.join(temp_dir, "preferences.json"))
            for _ in range(2):
                manager.record_selection(
                    EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
                )
            self.assertEqual("ask", manager.decision(
                EXCEL_FORMAT_PREFERENCE_KEY
            )["mode"])

            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
            )
            recommended = manager.decision(EXCEL_FORMAT_PREFERENCE_KEY)
            self.assertEqual("recommend", recommended["mode"])
            self.assertEqual("direct_format", recommended["preferred_method"])

            for _ in range(2):
                manager.record_selection(
                    EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
                )
            automatic = manager.decision(EXCEL_FORMAT_PREFERENCE_KEY)
            self.assertEqual("auto", automatic["mode"])
            self.assertEqual(1.0, automatic["confidence"])

    def test_alternative_choice_reduces_confidence(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preferences-") as temp_dir:
            manager = PreferenceManager(os.path.join(temp_dir, "preferences.json"))
            for _ in range(5):
                manager.record_selection(
                    EXCEL_FORMAT_PREFERENCE_KEY, "conditional_format"
                )
            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
            )

            decision = manager.decision(EXCEL_FORMAT_PREFERENCE_KEY)
            self.assertEqual("ask", decision["mode"])
            self.assertLess(decision["confidence"], 0.85)

    def test_explicit_remember_is_immediate_and_survives_reload(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preferences-") as temp_dir:
            path = os.path.join(temp_dir, "preferences.json")
            manager = PreferenceManager(path)
            record = manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "conditional_format", remember=True
            )

            self.assertTrue(record["locked"])
            reloaded = PreferenceManager(path)
            decision = reloaded.decision(EXCEL_FORMAT_PREFERENCE_KEY)
            self.assertEqual("auto", decision["mode"])
            self.assertEqual("explicitly_remembered", decision["reason"])

    def test_two_consecutive_failures_disable_auto_until_new_selection(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preferences-") as temp_dir:
            manager = PreferenceManager(os.path.join(temp_dir, "preferences.json"))
            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "conditional_format", remember=True
            )
            first = manager.record_failure(
                EXCEL_FORMAT_PREFERENCE_KEY, "conditional_format"
            )
            second = manager.record_failure(
                EXCEL_FORMAT_PREFERENCE_KEY, "conditional_format"
            )

            self.assertFalse(first["disabled"])
            self.assertTrue(second["disabled"])
            self.assertEqual("ask", manager.decision(
                EXCEL_FORMAT_PREFERENCE_KEY
            )["mode"])

            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
            )
            self.assertTrue(manager.get(
                EXCEL_FORMAT_PREFERENCE_KEY
            )["auto_apply_enabled"])

    def test_storage_is_json_and_creates_recoverable_backup(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preferences-") as temp_dir:
            path = os.path.join(temp_dir, "preferences.json")
            manager = PreferenceManager(path)
            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
            )
            manager.record_selection(
                EXCEL_FORMAT_PREFERENCE_KEY, "direct_format"
            )

            with open(path, "r", encoding="utf-8") as source:
                saved = json.load(source)
            self.assertEqual(1, saved["schema_version"])
            self.assertTrue(os.path.isfile(path + ".bak"))


class FailingConditionalAdapter:
    def __init__(self, base):
        self.base = base
        self.fail_conditional_execute = False

    def prepare(self, operation, params):
        return self.base.prepare(operation, params)

    def execute(self, prepared):
        if (
            self.fail_conditional_execute
            and prepared.operation == "apply_conditional_format"
        ):
            raise AppActionVerificationError("조건부 서식 검증 실패 모의")
        return self.base.execute(prepared)


class PreferenceParserFlowTests(unittest.TestCase):
    @staticmethod
    def _parser(temp_dir, excel, adapter=None, preference_path=None):
        parser = CommandParser()
        parser.execution_controller = ExecutionController(
            os.path.join(temp_dir, "diagnostics.json")
        )
        parser.preference_manager = PreferenceManager(
            preference_path or os.path.join(temp_dir, "user_preferences.json")
        )
        parser.app_action_registry = AppActionRegistry({
            "excel": adapter or adapter_for(excel)
        })
        parser.action_executor.controller = parser.execution_controller
        parser.action_executor.app_action_registry = parser.app_action_registry
        parser.macro_runner.controller = parser.execution_controller
        return parser

    @staticmethod
    def _ambiguous_command():
        return "엑셀 매출 열에서 50 이상을 노란색으로 표시해줘"

    def test_checkbox_remember_executes_and_next_request_skips_question(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="remember-box"
                )
                completed = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "conditional_format",
                    "remember-box",
                    True,
                )
                repeated = command_api.parse_command(
                    self._ambiguous_command(), session_id="remember-box"
                )

        self.assertTrue(first["data"]["confirmation"]["rememberable"])
        self.assertTrue(completed["success"])
        self.assertTrue(completed["data"]["preference_learning"]["locked"])
        self.assertTrue(repeated["success"])
        self.assertNotEqual("confirmation_required", repeated["status"])
        self.assertEqual(
            "conditional_format",
            repeated["data"]["preference_auto_applied"]["method"],
        )

    def test_korean_forward_phrase_remembers_without_checkbox(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="remember-text"
                )
                completed = command_api.parse_command(
                    "앞으로는 자동으로", session_id="remember-text"
                )

        self.assertEqual("clarification_required", first["status"])
        self.assertTrue(completed["success"])
        self.assertTrue(parser.preference_manager.get(
            EXCEL_FORMAT_PREFERENCE_KEY
        )["locked"])

    def test_five_choices_make_sixth_request_automatic(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                for index in range(5):
                    response = command_api.parse_command(
                        self._ambiguous_command(), session_id="repeat-learning"
                    )
                    self.assertEqual("clarification_required", response["status"])
                    options = response["data"]["confirmation"]["options"]
                    if index >= 3:
                        direct = next(
                            item for item in options
                            if item["id"] == "direct_format"
                        )
                        self.assertTrue(direct["recommended"])
                    completed = command_api.resolve_confirmation(
                        response["data"]["confirmation"]["confirmation_id"],
                        "direct_format",
                        "repeat-learning",
                    )
                    self.assertTrue(completed["success"])
                sixth = command_api.parse_command(
                    self._ambiguous_command(), session_id="repeat-learning"
                )

        self.assertTrue(sixth["success"])
        self.assertEqual(
            "direct_format", sixth["data"]["preference_auto_applied"]["method"]
        )

    def test_restart_reuses_saved_preference(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            path = os.path.join(temp_dir, "user_preferences.json")
            excel = FakeExcel()
            seed_sales(excel)
            first_parser = self._parser(temp_dir, excel, preference_path=path)
            with mock.patch.object(command_api, "parser", first_parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="before-restart"
                )
                command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "direct_format",
                    "before-restart",
                    True,
                )

            second_parser = self._parser(temp_dir, excel, preference_path=path)
            with mock.patch.object(command_api, "parser", second_parser):
                after_restart = command_api.parse_command(
                    self._ambiguous_command(), session_id="after-restart"
                )

        self.assertTrue(after_restart["success"])
        self.assertEqual(
            "direct_format",
            after_restart["data"]["preference_auto_applied"]["method"],
        )

    def test_explicit_forward_command_changes_a_locked_preference(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="preference-change"
                )
                command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "conditional_format",
                    "preference-change",
                    True,
                )
                changed = command_api.parse_command(
                    "엑셀 매출 열에서 50 이상을 앞으로는 지금만 빨간색으로 표시해줘",
                    session_id="preference-change",
                )
                repeated = command_api.parse_command(
                    self._ambiguous_command(), session_id="preference-change"
                )

        self.assertTrue(changed["success"])
        self.assertTrue(changed["data"]["preference_learning"]["locked"])
        self.assertEqual(
            "direct_format",
            repeated["data"]["preference_auto_applied"]["method"],
        )

    def test_cancel_does_not_learn_even_when_checkbox_is_true(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            seed_sales(excel)
            parser = self._parser(temp_dir, excel)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="cancel-learning"
                )
                cancelled = command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "cancel",
                    "cancel-learning",
                    True,
                )

        self.assertEqual("cancelled", cancelled["status"])
        self.assertIsNone(parser.preference_manager.get(
            EXCEL_FORMAT_PREFERENCE_KEY
        ))

    def test_two_auto_failures_stop_auto_and_ask_without_forcing_alternative(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-preference-flow-") as temp_dir:
            excel = FakeExcel()
            sheet = seed_sales(excel)
            failing = FailingConditionalAdapter(adapter_for(excel))
            parser = self._parser(temp_dir, excel, adapter=failing)
            with mock.patch.object(command_api, "parser", parser):
                first = command_api.parse_command(
                    self._ambiguous_command(), session_id="auto-failure"
                )
                command_api.resolve_confirmation(
                    first["data"]["confirmation"]["confirmation_id"],
                    "conditional_format",
                    "auto-failure",
                    True,
                )
                failing.fail_conditional_execute = True
                failed_once = command_api.parse_command(
                    self._ambiguous_command(), session_id="auto-failure"
                )
                failed_twice = command_api.parse_command(
                    self._ambiguous_command(), session_id="auto-failure"
                )

        self.assertEqual("failed", failed_once["status"])
        self.assertEqual("clarification_required", failed_twice["status"])
        self.assertEqual(
            "auto_apply_disabled_after_failures",
            parser.preference_manager.decision(
                EXCEL_FORMAT_PREFERENCE_KEY
            )["reason"],
        )
        self.assertEqual(-4142, sheet.Range("A3").Interior.ColorIndex)
        option_ids = {
            item["id"] for item in failed_twice["data"]["confirmation"]["options"]
        }
        self.assertIn("direct_format", option_ids)


if __name__ == "__main__":
    unittest.main()
