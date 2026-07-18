"""Tests for validated common Windows action plans."""

import os
import tempfile
import unittest
from unittest import mock

import win32con

from engine.action_executor import (
    ActionExecutor, ActionPlanError, ActionPlanVerificationError,
    ActionTargetNotFoundError,
)
from engine.builtins import BuiltinMacros
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.parser import CommandParser


def _step(action, **values):
    result = {
        "action": action, "target": "", "direction": "", "keys": [], "text": "",
        "seconds": 0, "x": 0, "y": 0, "width": 0, "height": 0,
    }
    result.update(values)
    return result


def _action_plan_result():
    return {
        "response": "창을 이동했습니다.",
        "actions": [{
            "action": "action_plan",
            "target": "window-layout",
            "app_name": "시스템",
            "macro_name": "창 공통 이동",
            "description": "앱 창을 지정 방향으로 이동",
            "code": "",
            "explanation_steps": [],
            "plan": [_step("move_window", target="{app}", direction="{direction}")],
            "learning": {
                "intent": "MOVE_WINDOW",
                "argument_mode": "json",
                "verbs": ["옮겨", "이동해"],
                "nouns": [{"text": "계산기", "canonical": "계산기", "type": "app"}],
                "utterances": [
                    "계산기를 오른쪽으로 옮겨",
                    "{app}을 {direction}으로 옮겨",
                ],
                "slots": [
                    {"name": "app", "type": "app", "value": "계산기", "required": True},
                    {"name": "direction", "type": "direction", "value": "오른쪽", "required": True},
                ],
            },
        }],
    }


class ActionExecutorTests(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor({"메모장": "notepad", "계산기": "calc"})

    def test_all_steps_are_preflighted_before_first_external_action(self):
        plan = [
            _step("open_app", target="메모장"),
            _step("navigate_url", target="not-a-url"),
        ]
        with mock.patch.object(self.executor, "_open_app") as open_app:
            with self.assertRaises(ActionPlanError):
                self.executor.execute_plan(plan)
        open_app.assert_not_called()

    def test_console_open_target_is_blocked_during_plan_preflight(self):
        executor = ActionExecutor({
            "메모장": "notepad",
            "echo": r"C:\Program Files\Git\usr\bin\echo.exe",
        })
        plan = [
            _step("open_app", target="메모장"),
            _step("open_app", target="echo"),
        ]
        with mock.patch.object(executor, "_open_app") as open_app:
            with self.assertRaises(ActionPlanError):
                executor.execute_plan(plan)
        open_app.assert_not_called()

    def test_common_actions_route_to_expected_boundaries(self):
        plan = [
            _step("open_app", target="{app}"),
            _step("focus_window", target="{app}"),
            _step("move_window", target="{app}", direction="{direction}"),
            _step("window_state", target="{app}", direction="최대화"),
            _step("hotkey", keys=["ctrl", "s"]),
            _step("type_text", text="{text}"),
            _step("wait", seconds=0.1),
            _step("navigate_url", target="https://example.com"),
        ]
        slots = {"app": "메모장", "direction": "왼쪽", "text": "테스트"}
        with mock.patch.object(self.executor, "_open_app") as open_app, \
             mock.patch.object(self.executor, "_focus_window") as focus, \
             mock.patch.object(self.executor, "_move_window") as move, \
             mock.patch.object(self.executor, "_window_state") as state, \
             mock.patch("engine.action_executor.press_hotkey") as hotkey, \
             mock.patch.object(self.executor, "_type_text") as type_text, \
             mock.patch("engine.action_executor.time.sleep") as sleep, \
             mock.patch("engine.action_executor.os.startfile") as startfile, \
             mock.patch.object(
                 self.executor, "_verify_step",
                 return_value={"status": "confirmation_required", "reason": "test"},
             ):
            result = self.executor.execute_plan(plan, slots)

        self.assertTrue(result["success"])
        self.assertEqual("confirmation_required", result["verification_status"])
        open_app.assert_called_once_with("메모장")
        focus.assert_called_once_with("메모장")
        move.assert_called_once_with("메모장", "왼쪽", 0, 0, 0, 0)
        state.assert_called_once_with("메모장", "최대화")
        hotkey.assert_called_once_with(["ctrl", "s"])
        type_text.assert_called_once_with("테스트")
        sleep.assert_called_once_with(0.1)
        startfile.assert_called_once_with("https://example.com")

    def test_targeted_type_text_focuses_before_input(self):
        plan = [_step("type_text", target="메모장", text="테스트")]
        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch.object(self.executor, "_focus_window") as focus, \
             mock.patch.object(self.executor, "_type_text") as type_text, \
             mock.patch(
                 "engine.action_executor.win32gui.GetForegroundWindow",
                 side_effect=[20, 10],
             ):
            result = self.executor.execute_plan(plan)

        self.assertTrue(result["success"])
        focus.assert_called_once_with("메모장")
        type_text.assert_called_once_with("테스트")

    def test_targeted_type_text_stops_when_focus_cannot_be_verified(self):
        plan = [_step("type_text", target="메모장", text="입력되면 안 됨")]
        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch.object(self.executor, "_focus_window"), \
             mock.patch.object(self.executor, "_type_text") as type_text, \
             mock.patch(
                 "engine.action_executor.win32gui.GetForegroundWindow",
                 return_value=20,
             ):
            with self.assertRaises(ActionPlanVerificationError):
                self.executor.execute_plan(plan)

        type_text.assert_not_called()

    def test_focus_window_uses_attached_input_when_direct_request_is_rejected(self):
        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch("engine.action_executor.win32gui.ShowWindow"), \
             mock.patch("engine.action_executor.win32gui.BringWindowToTop"), \
             mock.patch(
                 "engine.action_executor.win32gui.SetForegroundWindow",
                 side_effect=[OSError("denied"), None],
             ) as set_foreground, \
             mock.patch(
                 "engine.action_executor.win32gui.GetForegroundWindow",
                 side_effect=[20, 20, 20, 10, 10],
             ), \
             mock.patch(
                 "engine.action_executor.win32process.GetWindowThreadProcessId",
                 side_effect=[(200, 2), (100, 1)],
             ), \
             mock.patch(
                 "engine.action_executor.win32api.GetCurrentThreadId",
                 return_value=300,
             ), \
             mock.patch(
                 "engine.action_executor.win32process.AttachThreadInput"
             ) as attach, \
             mock.patch("engine.action_executor.win32gui.SetActiveWindow"), \
             mock.patch("engine.action_executor.win32gui.SetFocus"):
            focused = self.executor._focus_window("메모장")

        self.assertEqual(10, focused)
        self.assertEqual(2, set_foreground.call_count)
        self.assertEqual([
            mock.call(300, 200, True),
            mock.call(300, 100, True),
            mock.call(300, 100, False),
            mock.call(300, 200, False),
        ], attach.call_args_list)

    def test_focus_window_when_ready_waits_for_new_window(self):
        with mock.patch.object(
            self.executor,
            "_focus_window",
            side_effect=[ActionTargetNotFoundError("not ready"), 10],
        ) as focus, mock.patch("engine.action_executor.time.sleep") as sleep:
            focused = self.executor.focus_window_when_ready("메모장", timeout=0.5)

        self.assertEqual(10, focused)
        self.assertEqual(2, focus.call_count)
        sleep.assert_called_once_with(0.05)

    def test_focus_window_reports_verified_failure_after_all_fallbacks(self):
        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch("engine.action_executor.win32gui.ShowWindow"), \
             mock.patch("engine.action_executor.win32gui.BringWindowToTop"), \
             mock.patch("engine.action_executor.win32gui.SetForegroundWindow"), \
             mock.patch(
                 "engine.action_executor.win32gui.GetForegroundWindow",
                 return_value=20,
             ), \
             mock.patch(
                 "engine.action_executor.win32process.GetWindowThreadProcessId",
                 side_effect=[(200, 2), (100, 1)],
             ), \
             mock.patch(
                 "engine.action_executor.win32api.GetCurrentThreadId",
                 return_value=300,
             ), \
             mock.patch(
                 "engine.action_executor.win32process.AttachThreadInput"
             ), \
             mock.patch("engine.action_executor.win32gui.SetActiveWindow"), \
             mock.patch("engine.action_executor.win32gui.SetFocus"), \
             mock.patch("engine.action_executor.win32api.keybd_event"), \
             mock.patch("engine.action_executor.win32gui.FlashWindow"):
            with self.assertRaises(ActionPlanVerificationError) as raised:
                self.executor._focus_window("메모장")

        self.assertIn("전면 전환", str(raised.exception))

    def test_type_text_waits_for_paste_delivery_before_restoring_clipboard(self):
        events = []
        self.executor.controller = mock.Mock()
        self.executor.controller.wait.side_effect = (
            lambda seconds: events.append(("wait", seconds))
        )

        with mock.patch(
            "engine.action_executor.win32clipboard.OpenClipboard",
            side_effect=lambda: events.append("open"),
        ), mock.patch(
            "engine.action_executor.win32clipboard.CloseClipboard",
            side_effect=lambda: events.append("close"),
        ), mock.patch(
            "engine.action_executor.win32clipboard.IsClipboardFormatAvailable",
            return_value=True,
        ), mock.patch(
            "engine.action_executor.win32clipboard.GetClipboardData",
            side_effect=["기존 클립보드", "새 입력"],
        ), mock.patch(
            "engine.action_executor.win32clipboard.EmptyClipboard",
            side_effect=lambda: events.append("empty"),
        ), mock.patch(
            "engine.action_executor.win32clipboard.SetClipboardText",
            side_effect=lambda value, *_: events.append(("set", value)),
        ), mock.patch(
            "engine.action_executor.press_hotkey",
            side_effect=lambda keys: events.append(("hotkey", tuple(keys))),
        ):
            self.executor._type_text("새 입력")

        paste_index = events.index(("hotkey", ("ctrl", "v")))
        restore_index = events.index(("set", "기존 클립보드"))
        self.assertLess(paste_index, restore_index)
        self.assertIn(("wait", 0.15), events[paste_index - 1:paste_index])
        self.assertIn(("wait", 0.35), events[paste_index + 1:restore_index])

    def test_type_text_does_not_overwrite_new_user_clipboard_value(self):
        with mock.patch(
            "engine.action_executor.win32clipboard.OpenClipboard"
        ), mock.patch(
            "engine.action_executor.win32clipboard.CloseClipboard"
        ), mock.patch(
            "engine.action_executor.win32clipboard.IsClipboardFormatAvailable",
            return_value=True,
        ), mock.patch(
            "engine.action_executor.win32clipboard.GetClipboardData",
            side_effect=["기존 값", "사용자가 새로 복사한 값"],
        ), mock.patch(
            "engine.action_executor.win32clipboard.SetClipboardText"
        ) as set_text, mock.patch(
            "engine.action_executor.win32clipboard.EmptyClipboard"
        ), mock.patch(
            "engine.action_executor.press_hotkey"
        ), mock.patch.object(
            self.executor, "_input_delivery_wait"
        ):
            self.executor._type_text("임시 입력")

        self.assertEqual(1, set_text.call_count)
        set_text.assert_called_once_with("임시 입력", win32con.CF_UNICODETEXT)

    def test_right_half_uses_current_screen_dimensions(self):
        with mock.patch.object(self.executor, "_find_window", return_value=99), \
             mock.patch("engine.action_executor.win32api.GetSystemMetrics", side_effect=[1920, 1080]), \
             mock.patch("engine.action_executor.win32gui.MoveWindow") as move:
            self.executor._move_window("계산기", "오른쪽", 0, 0, 0, 0)
        move.assert_called_once_with(99, 960, 0, 960, 1080, True)

    def test_unknown_slot_is_rejected(self):
        issue = self.executor.validate_plan(
            [_step("open_app", target="{missing}")], slot_names={"app"}
        )
        self.assertIn("등록되지 않은 슬롯", issue)

    def test_invalid_hotkey_is_rejected_before_prior_open_step(self):
        plan = [
            _step("open_app", target="메모장"),
            _step("hotkey", keys=["not-a-real-key"]),
        ]
        with mock.patch.object(self.executor, "_open_app") as open_app:
            with self.assertRaises(ActionPlanError):
                self.executor.execute_plan(plan)
        open_app.assert_not_called()

    def test_fully_verifiable_plan_reports_verified(self):
        with mock.patch("engine.action_executor.time.sleep"):
            result = self.executor.execute_plan([_step("wait", seconds=0.1)])
        self.assertEqual("verified", result["verification_status"])
        self.assertEqual("verified", result["verification"][0]["status"])

    def test_move_verification_compares_actual_window_rectangle(self):
        step = _step("move_window", target="메모장", direction="오른쪽")
        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch("engine.action_executor.win32api.GetSystemMetrics", side_effect=[1920, 1080]), \
             mock.patch("engine.action_executor.win32gui.GetWindowRect", return_value=(960, 0, 1920, 1080)):
            passed = self.executor._verify_step(step)
        self.assertEqual("verified", passed["status"])

        with mock.patch.object(self.executor, "_find_window", return_value=10), \
             mock.patch("engine.action_executor.win32api.GetSystemMetrics", side_effect=[1920, 1080]), \
             mock.patch("engine.action_executor.win32gui.GetWindowRect", return_value=(0, 0, 500, 500)):
            failed = self.executor._verify_step(step)
        self.assertEqual("failed", failed["status"])

    def test_failed_verification_raises_with_completed_steps(self):
        plan = [_step("open_app", target="메모장")]
        with mock.patch.object(self.executor, "_open_app"), \
             mock.patch.object(
                 self.executor, "_verify_step",
                 return_value={"status": "failed", "reason": "창 없음"},
             ):
            with self.assertRaises(ActionPlanVerificationError) as raised:
                self.executor.execute_plan(plan)
        self.assertEqual(["open_app"], raised.exception.completed)


class ActionPlanLearningTests(unittest.TestCase):
    def _parser(self, path):
        parser = CommandParser()
        parser.dict_mgr = DictionaryManager(path)
        parser.llm_engine = LLMEngine(parser.dict_mgr)
        parser.builtins = BuiltinMacros(parser.dict_mgr, parser)
        return parser

    def test_learned_plan_reuses_new_slots_without_ai_or_python(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-plan-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.dict_mgr.add_custom_noun("계산기", "calc")
            parser.dict_mgr.add_custom_noun("메모장", "notepad")
            parser.llm_engine.process_command = mock.Mock(return_value=_action_plan_result())
            with mock.patch.object(
                parser.action_executor, "execute_plan", return_value={
                    "success": True,
                    "verification_status": "verified",
                    "verification": [{"step": 1, "status": "verified"}],
                }
            ) as execute, mock.patch("engine.parser.subprocess.run") as python_run:
                first = parser.parse_and_execute("계산기를 오른쪽으로 옮겨", use_api=True)
                self.assertIn("학습", first)
                self.assertEqual("계산기", execute.call_args.args[1]["app"])
                parser.parse_and_execute("응", use_api=True)
                saved = parser.dict_mgr.learned_macros["시스템"]["창_공통_이동"]
                self.assertTrue(saved["plan"])
                self.assertFalse(saved["code"])
                self.assertEqual("verified", saved["verification_status"])

                parser.llm_engine.process_command.reset_mock()
                waiting = parser.execute_command_result(
                    "메모장을 왼쪽으로 옮겨",
                    use_api=True,
                    session_id="plan-policy",
                )
                self.assertEqual("confirmation_required", waiting["status"])
                response = parser.resolve_pending_confirmation(
                    "plan-policy",
                    waiting["data"]["confirmation"]["confirmation_id"],
                    "run_once",
                )
                reused_slots = execute.call_args.args[1]

            self.assertTrue(response["success"])
            self.assertEqual("메모장", reused_slots["app"])
            self.assertEqual("왼쪽", reused_slots["direction"])
            parser.llm_engine.process_command.assert_not_called()
            python_run.assert_not_called()

    def test_failed_result_verification_never_becomes_learning_candidate(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-plan-test-") as temp_dir:
            parser = self._parser(os.path.join(temp_dir, "dictionaries.json"))
            parser.llm_engine.process_command = mock.Mock(return_value=_action_plan_result())
            with mock.patch.object(
                parser.action_executor,
                "execute_plan",
                side_effect=ActionPlanVerificationError("창 위치 불일치"),
            ):
                response = parser.parse_and_execute(
                    "계산기를 오른쪽으로 옮겨", use_api=True
                )
        self.assertFalse(parser.pending_macros)
        self.assertNotIn("[응/아니오]", response)
        self.assertIn("학습하지 않았습니다", response)


if __name__ == "__main__":
    unittest.main()
