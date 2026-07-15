import os
import tempfile
import unittest
from unittest import mock

from engine.action_executor import (
    ActionConfirmationRequired, ActionExecutor, ActionPlanError,
)
from engine.action_registry import ALLOWED_ACTIONS, action_spec
from engine.parser import CommandParser


class ActionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor({"메모장": "notepad"})

    def test_registry_contains_clipboard_and_file_actions(self):
        expected = {
            "clipboard_set", "clipboard_get", "copy_file", "move_file",
            "create_folder", "write_text_file", "app_command",
        }
        self.assertTrue(expected.issubset(ALLOWED_ACTIONS))
        self.assertFalse(action_spec("copy_file")["retryable"])
        self.assertFalse(action_spec("move_file")["retryable"])
        self.assertFalse(action_spec("open_app")["retryable"])
        self.assertFalse(action_spec("uia_click")["retryable"])
        self.assertFalse(action_spec("app_command")["retryable"])
        self.assertTrue(action_spec("app_command")["native_action"])
        self.assertEqual(1, action_spec("focus_window")["max_retries"])

    def test_clipboard_actions_return_verified_result_and_output(self):
        with mock.patch.object(self.executor, "_clipboard_set") as set_clip, \
             mock.patch.object(self.executor, "_clipboard_get", return_value="복사 내용"):
            result = self.executor.execute_plan([
                {"action": "clipboard_set", "text": "복사 내용"},
                {"action": "clipboard_get"},
            ])
        set_clip.assert_called_once_with("복사 내용")
        self.assertEqual("verified", result["verification_status"])
        self.assertEqual("복사 내용", result["step_results"][0]["output"])

    def test_file_actions_execute_and_verify(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-file-actions-") as temp_dir:
            source = os.path.join(temp_dir, "source.txt")
            copied = os.path.join(temp_dir, "copied.txt")
            moved = os.path.join(temp_dir, "moved.txt")
            folder = os.path.join(temp_dir, "new-folder")
            written = os.path.join(temp_dir, "written.txt")
            with open(source, "w", encoding="utf-8") as file:
                file.write("원본")

            result = self.executor.execute_plan([
                {"action": "copy_file", "target": source, "text": copied},
                {"action": "move_file", "target": copied, "text": moved},
                {"action": "create_folder", "target": folder},
                {"action": "write_text_file", "target": written, "text": "저장 내용"},
            ])

            self.assertTrue(os.path.isfile(source))
            self.assertFalse(os.path.exists(copied))
            self.assertTrue(os.path.isfile(moved))
            self.assertTrue(os.path.isdir(folder))
            with open(written, "r", encoding="utf-8") as file:
                self.assertEqual("저장 내용", file.read())
            self.assertEqual("verified", result["verification_status"])

    def test_invalid_file_step_is_rejected_before_open_app(self):
        missing = os.path.join(tempfile.gettempdir(), "jarvis-definitely-missing.txt")
        with mock.patch.object(self.executor, "_open_app") as open_app:
            with self.assertRaises(ActionPlanError):
                self.executor.execute_plan([
                    {"action": "open_app", "target": "메모장"},
                    {"action": "copy_file", "target": missing, "text": tempfile.gettempdir()},
                ])
        open_app.assert_not_called()

    def test_existing_files_require_explicit_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-overwrite-test-") as temp_dir:
            source = os.path.join(temp_dir, "source.txt")
            destination = os.path.join(temp_dir, "destination.txt")
            with open(source, "w", encoding="utf-8") as file:
                file.write("새 내용")
            with open(destination, "w", encoding="utf-8") as file:
                file.write("기존 내용")

            with self.assertRaises(ActionConfirmationRequired):
                self.executor.execute_plan([{
                    "action": "copy_file", "target": source,
                    "text": destination, "overwrite": False,
                }])
            with open(destination, encoding="utf-8") as file:
                self.assertEqual("기존 내용", file.read())

            self.executor.execute_plan([{
                "action": "copy_file", "target": source,
                "text": destination, "overwrite": True,
            }])
            with open(destination, encoding="utf-8") as file:
                self.assertEqual("새 내용", file.read())

    def test_move_and_write_do_not_silently_replace_files(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-overwrite-test-") as temp_dir:
            source = os.path.join(temp_dir, "move-source.txt")
            destination = os.path.join(temp_dir, "existing.txt")
            with open(source, "w", encoding="utf-8") as file:
                file.write("이동 내용")
            with open(destination, "w", encoding="utf-8") as file:
                file.write("기존 내용")

            with self.assertRaises(ActionConfirmationRequired):
                self.executor.execute_plan([{
                    "action": "move_file", "target": source,
                    "text": destination,
                }])
            self.assertTrue(os.path.isfile(source))
            with self.assertRaises(ActionConfirmationRequired):
                self.executor.execute_plan([{
                    "action": "write_text_file", "target": destination,
                    "text": "저장 내용",
                }])

            self.executor.execute_plan([{
                "action": "move_file", "target": source,
                "text": destination, "overwrite": True,
            }])
            self.assertFalse(os.path.exists(source))
            with open(destination, encoding="utf-8") as file:
                self.assertEqual("이동 내용", file.read())
            self.executor.execute_plan([{
                "action": "write_text_file", "target": destination,
                "text": "저장 내용", "overwrite": True,
            }])
            with open(destination, encoding="utf-8") as file:
                self.assertEqual("저장 내용", file.read())

    def test_confirmation_preflight_blocks_earlier_external_action(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-overwrite-test-") as temp_dir:
            destination = os.path.join(temp_dir, "existing.txt")
            with open(destination, "w", encoding="utf-8") as file:
                file.write("기존")
            with mock.patch.object(self.executor, "_open_app") as open_app:
                with self.assertRaises(ActionConfirmationRequired):
                    self.executor.execute_plan([
                        {"action": "open_app", "target": "메모장"},
                        {"action": "write_text_file", "target": destination, "text": "새 값"},
                    ])
            open_app.assert_not_called()

    def test_parser_returns_confirmation_required_for_existing_file(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-overwrite-test-") as temp_dir:
            destination = os.path.join(temp_dir, "existing.txt")
            with open(destination, "w", encoding="utf-8") as file:
                file.write("기존")
            parser = CommandParser()
            parser.llm_engine.process_command = mock.Mock(return_value={
                "response": "파일을 저장합니다.",
                "actions": [{
                    "action": "action_plan", "target": destination,
                    "app_name": "시스템", "macro_name": "save_test",
                    "description": "파일 저장", "code": "",
                    "explanation_steps": [],
                    "learning": {
                        "intent": "WRITE_FILE", "argument_mode": "json",
                        "verbs": ["저장"], "nouns": [],
                        "utterances": ["파일 저장"], "slots": [],
                    },
                    "plan": [{
                        "action": "write_text_file", "target": destination,
                        "text": "새 값", "overwrite": False,
                    }],
                }],
            })
            result = parser.execute_command_result("특수 파일 저장 요청")
            self.assertFalse(result["success"])
            self.assertEqual("confirmation_required", result["status"])
            self.assertEqual([], parser.pending_macros)
            with open(destination, encoding="utf-8") as file:
                self.assertEqual("기존", file.read())


if __name__ == "__main__":
    unittest.main()
