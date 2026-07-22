import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from engine.action_executor import ActionExecutor, ActionPlanError
from engine.action_registry import ALLOWED_ACTIONS
from engine.parser import CommandParser
from engine.ui_automation import (
    UIAutomationAmbiguousTarget,
    UIAutomationError,
    UIAutomationSearchLimit,
    UIAutomationSearchTimeout,
    UIAutomationTargetChanged,
    UIAutomationTargetNotFound,
    WindowsUIAutomation,
    normalize_accessible_name,
)


class FakeControl:
    def __init__(
        self, name, control_type="Button", automation_id="", parent=None
    ):
        self._name = name
        self.element_info = SimpleNamespace(
            name=name,
            control_type=control_type,
            automation_id=automation_id,
        )
        self._parent = parent
        self.click_count = 0
        self.text = ""

    def window_text(self):
        return self._name

    def parent(self):
        return self._parent

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def click_input(self):
        self.click_count += 1

    def set_focus(self):
        return None

    def set_edit_text(self, value):
        self.text = str(value)

    def get_value(self):
        return self.text


class FakeWindow(FakeControl):
    def __init__(self, controls, name="테스트 창"):
        super().__init__(name, "Window")
        self.controls = list(controls)
        self.handle = 101
        self.depths = []

    def descendants(self, depth=None):
        self.depths.append(depth)
        return list(self.controls)

    def process_id(self):
        return 202


class AdvancingClock:
    def __init__(self, step=0.02):
        self.value = 0.0
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


class UIAutomationPilotTests(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor({"메모장": "notepad"})

    def test_registry_exposes_three_pilot_actions(self):
        self.assertTrue({"uia_click", "uia_set_text", "uia_select_file"}.issubset(ALLOWED_ACTIONS))

    def test_uia_set_text_and_click_are_dispatched(self):
        self.executor.ui_automation = mock.Mock()
        self.executor.ui_automation.set_text.return_value = {
            "success": True, "status": "verified", "control": "Text Editor"
        }
        self.executor.ui_automation.click.return_value = {
            "success": True, "status": "confirmation_required", "control": "저장"
        }
        result = self.executor.execute_plan([
            {"action": "uia_set_text", "target": "메모장", "direction": "", "text": "안녕하세요"},
            {"action": "uia_click", "target": "메모장", "direction": "저장"},
        ])
        self.executor.ui_automation.set_text.assert_called_once_with("메모장", "", "안녕하세요")
        self.executor.ui_automation.click.assert_called_once_with("메모장", "저장")
        self.assertEqual("confirmation_required", result["verification_status"])

    def test_explorer_file_selection_requires_real_file_before_ui_action(self):
        missing = os.path.join(tempfile.gettempdir(), "jarvis-uia-missing.txt")
        self.executor.ui_automation = mock.Mock()
        with self.assertRaises(ActionPlanError):
            self.executor.execute_plan([{"action": "uia_select_file", "target": missing}])
        self.executor.ui_automation.select_explorer_file.assert_not_called()

    def test_unknown_registered_app_is_rejected_before_ui_lookup(self):
        adapter = WindowsUIAutomation({"메모장": "notepad"})
        with self.assertRaises(UIAutomationError):
            adapter.find_window("없는앱")

    def test_title_match_wins_over_another_window_from_the_same_process(self):
        adapter = WindowsUIAutomation({"고유 문서": "notepad"})
        other = mock.Mock(handle=10)
        other.window_text.return_value = "다른 문서 - 메모장"
        other.process_id.return_value = 101
        other.is_visible.return_value = True
        wanted = mock.Mock(handle=11)
        wanted.window_text.return_value = "고유 문서 - 메모장"
        wanted.process_id.return_value = 102
        wanted.is_visible.return_value = True
        desktop = mock.Mock()
        desktop.windows.return_value = [other, wanted]

        with mock.patch("engine.ui_automation.Desktop", return_value=desktop), \
             mock.patch.object(adapter, "_process_ids", return_value={101, 102}), \
             mock.patch.object(adapter, "_preferred_window", side_effect=lambda values: values[0]):
            selected = adapter.find_window("고유 문서")

        self.assertIs(wanted, selected)


class StructuredUIAutomationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WindowsUIAutomation({"테스트": "test.exe"})

    def locate(self, controls, selector, *, editable=False):
        return self.adapter._locate_control(
            FakeWindow(controls), selector, editable=editable
        )

    def test_name_normalization_is_deterministic(self):
        self.assertEqual("save", normalize_accessible_name("  &Save… "))
        self.assertEqual("hello world", normalize_accessible_name("HELLO   WORLD!"))
        self.assertNotEqual("save", normalize_accessible_name("safe"))

    def test_automation_id_exact_has_highest_priority(self):
        other = FakeControl("저장", automation_id="secondary")
        wanted = FakeControl("다른 이름", automation_id="saveButton")
        control, diagnostic = self.locate(
            [other, wanted],
            {
                "automation_id": "saveButton",
                "name": "",
                "control_type": "Button",
                "match_mode": "auto",
            },
        )
        self.assertIs(wanted, control)
        self.assertEqual("automation_id", diagnostic["locator_strategy"])
        self.assertEqual(1, diagnostic["fallback_level"])

    def test_exact_normalized_and_partial_name_ladder(self):
        exact, exact_diag = self.locate(
            [FakeControl("저장")],
            {"name": "저장", "control_type": "Button", "match_mode": "auto"},
        )
        self.assertEqual("저장", exact.window_text())
        self.assertEqual(2, exact_diag["fallback_level"])

        normalized, normalized_diag = self.locate(
            [FakeControl("&SAVE…")],
            {"name": "save", "control_type": "Button", "match_mode": "auto"},
        )
        self.assertEqual("&SAVE…", normalized.window_text())
        self.assertEqual(3, normalized_diag["fallback_level"])

        partial, partial_diag = self.locate(
            [FakeControl("문서 저장하기")],
            {"name": "저장", "control_type": "Button", "match_mode": "contains"},
        )
        self.assertEqual("문서 저장하기", partial.window_text())
        self.assertEqual(4, partial_diag["fallback_level"])

    def test_parent_and_ancestor_context_disambiguate(self):
        root = FakeControl("설정")
        left = FakeControl("왼쪽 영역", "Pane", parent=root)
        right = FakeControl("오른쪽 영역", "Pane", parent=root)
        left_save = FakeControl("저장", parent=left)
        right_save = FakeControl("저장", parent=right)
        right_save_as = FakeControl("다른 이름으로 저장", parent=right)
        selected, diagnostic = self.locate(
            [left_save, right_save, right_save_as],
            {
                "name": "저장",
                "control_type": "Button",
                "parent_name": "오른쪽 영역",
                "match_mode": "auto",
            },
        )
        self.assertIs(right_save, selected)
        self.assertEqual(5, diagnostic["fallback_level"])

        other_root = FakeControl("다른 설정")
        other_pane = FakeControl("오른쪽 영역", "Pane", parent=other_root)
        other_save = FakeControl("저장", parent=other_pane)
        ancestor_selected, ancestor_diag = self.locate(
            [right_save, other_save],
            {
                "name": "저장",
                "control_type": "Button",
                "ancestor_name": "설정",
                "match_mode": "auto",
            },
        )
        self.assertIs(right_save, ancestor_selected)
        self.assertEqual(6, ancestor_diag["fallback_level"])

    def test_equal_candidates_are_never_clicked(self):
        first_parent = FakeControl("첫째 영역", "Pane")
        second_parent = FakeControl("둘째 영역", "Pane")
        first = FakeControl("저장", parent=first_parent)
        second = FakeControl("저장", parent=second_parent)
        window = FakeWindow([first, second])
        with mock.patch.object(self.adapter, "find_window", return_value=window):
            with self.assertRaises(UIAutomationAmbiguousTarget) as raised:
                self.adapter.click(
                    "테스트",
                    {
                        "name": "저장",
                        "control_type": "Button",
                        "match_mode": "auto",
                    },
                )
        self.assertEqual(0, first.click_count)
        self.assertEqual(0, second.click_count)
        self.assertEqual(2, len(raised.exception.candidates))
        self.assertEqual("ambiguous_target", raised.exception.error_code)

    def test_timeout_stops_bounded_search(self):
        adapter = WindowsUIAutomation(
            {"테스트": "test.exe"},
            search_timeout=0.05,
            clock=AdvancingClock(),
        )
        controls = [FakeControl(f"버튼 {index}") for index in range(10)]
        with self.assertRaises(UIAutomationSearchTimeout) as raised:
            adapter._locate_control(
                FakeWindow(controls),
                {"name": "없음", "control_type": "Button", "match_mode": "auto"},
            )
        self.assertEqual("timeout", raised.exception.error_type)
        self.assertEqual("timeout", raised.exception.diagnostic["locator_strategy"])

    def test_truncated_tree_never_acts_on_an_unproven_unique_match(self):
        adapter = WindowsUIAutomation(
            {"테스트": "test.exe"}, max_controls=2
        )
        wanted = FakeControl("저장")
        with self.assertRaises(UIAutomationSearchLimit):
            adapter._locate_control(
                FakeWindow([wanted, FakeControl("열기"), FakeControl("저장")]),
                "저장",
            )
        self.assertEqual(0, wanted.click_count)

    def test_legacy_name_selector_and_cache_remain_compatible(self):
        control = FakeControl("저장")
        window = FakeWindow([control])
        selected, first = self.adapter._locate_control(window, "저장")
        again, cached = self.adapter._locate_control(window, "저장")
        self.assertIs(control, selected)
        self.assertIs(control, again)
        self.assertEqual(7, first["fallback_level"])
        self.assertTrue(cached["locator_strategy"].startswith("cache:"))
        self.assertEqual([8], window.depths)

    def test_not_found_has_typed_error_and_diagnostics(self):
        with self.assertRaises(UIAutomationTargetNotFound) as raised:
            self.locate(
                [FakeControl("열기")],
                {"name": "저장", "control_type": "Button", "match_mode": "exact"},
            )
        diagnostic = raised.exception.diagnostic
        self.assertEqual("target_not_found", raised.exception.error_type)
        self.assertEqual("not_found", diagnostic["locator_strategy"])
        self.assertEqual(8, diagnostic["fallback_level"])
        self.assertEqual(0, diagnostic["candidate_count"])
        self.assertEqual({
            "locator_strategy", "fallback_level", "candidate_count",
            "matched_name", "matched_control_type", "matched_automation_id",
            "search_duration_ms",
        }, set(diagnostic))

    def test_click_rediscovery_retries_once_on_the_same_window_before_action(self):
        missing = FakeWindow([FakeControl("열기")])
        wanted = FakeControl("저장")
        recovered = FakeWindow([wanted])
        with mock.patch.object(
            self.adapter, "find_window", side_effect=[missing, recovered]
        ) as find:
            result = self.adapter.click(
                "테스트",
                {"name": "저장", "control_type": "Button", "match_mode": "exact"},
            )

        self.assertEqual(2, find.call_count)
        self.assertEqual(1, wanted.click_count)
        recovery = result["pre_execution_recovery"]
        self.assertEqual("recovered", recovery["outcome"])
        self.assertEqual(1, recovery["retry_count"])
        self.assertFalse(recovery["execution_started"])
        self.assertTrue(recovery["target_unchanged"])

    def test_click_rediscovery_blocks_a_different_window_before_action(self):
        missing = FakeWindow([FakeControl("열기")])
        wanted = FakeControl("저장")
        changed = FakeWindow([wanted])
        changed.handle = 999
        with mock.patch.object(
            self.adapter, "find_window", side_effect=[missing, changed]
        ) as find:
            with self.assertRaises(UIAutomationTargetChanged) as raised:
                self.adapter.click("테스트", "저장")

        self.assertEqual(2, find.call_count)
        self.assertEqual(0, wanted.click_count)
        recovery = raised.exception.diagnostic["pre_execution_recovery"]
        self.assertEqual("target_changed", recovery["outcome"])
        self.assertFalse(recovery["target_resolved"])

    def test_click_rediscovery_exhausts_after_one_fresh_search(self):
        first = FakeWindow([FakeControl("열기")])
        second = FakeWindow([FakeControl("닫기")])
        with mock.patch.object(
            self.adapter, "find_window", side_effect=[first, second]
        ) as find:
            with self.assertRaises(UIAutomationTargetNotFound) as raised:
                self.adapter.click("테스트", "저장")

        self.assertEqual(2, find.call_count)
        self.assertEqual(2, self.adapter._search_count)
        recovery = raised.exception.diagnostic["pre_execution_recovery"]
        self.assertEqual("not_found", recovery["outcome"])
        self.assertEqual(1, recovery["retry_limit"])
        self.assertEqual(1, recovery["retry_count"])

    def test_click_failure_after_action_start_is_never_rediscovered(self):
        control = FakeControl("저장")
        control.click_input = mock.Mock(side_effect=RuntimeError("click failed"))
        window = FakeWindow([control])
        with mock.patch.object(
            self.adapter, "find_window", return_value=window
        ) as find:
            with self.assertRaises(RuntimeError):
                self.adapter.click("테스트", "저장")

        find.assert_called_once_with("테스트")

    def test_set_text_uses_the_same_pre_execution_recovery_contract(self):
        missing = FakeWindow([FakeControl("저장")])
        editor = FakeControl("내용", "Edit")
        recovered = FakeWindow([editor])
        with mock.patch.object(
            self.adapter, "find_window", side_effect=[missing, recovered]
        ):
            result = self.adapter.set_text("테스트", "내용", "안녕하세요")

        self.assertEqual("안녕하세요", editor.text)
        self.assertEqual("recovered", result["pre_execution_recovery"]["outcome"])

    def test_structured_selector_is_rendered_and_dispatched(self):
        executor = ActionExecutor({"테스트": "test.exe"})
        executor.ui_automation = mock.Mock()
        executor.ui_automation.click.return_value = {
            "success": True,
            "status": "confirmation_required",
        }
        selector = {
            "name": "{button}",
            "control_type": "Button",
            "match_mode": "normalized",
        }
        executor.execute_plan([{
            "action": "uia_click",
            "target": "테스트",
            "direction": "",
            "selector": selector,
        }], {"button": "저장"})
        executor.ui_automation.click.assert_called_once_with(
            "테스트",
            {
                "name": "저장",
                "control_type": "Button",
                "match_mode": "normalized",
            },
        )

    def test_click_requires_legacy_or_structured_locator(self):
        executor = ActionExecutor({"테스트": "test.exe"})
        validation = executor.validate_plan([{
            "action": "uia_click",
            "target": "테스트",
            "direction": "",
            "selector": {},
        }])
        self.assertIn("selector", validation)

    def test_ambiguous_choice_card_resumes_from_failed_step(self):
        parser = CommandParser()
        ambiguity = UIAutomationAmbiguousTarget(
            "저장 버튼이 두 개입니다.",
            candidates=[
                {
                    "name": "저장",
                    "control_type": "Button",
                    "automation_id": "",
                    "parent_name": "왼쪽 영역",
                    "ancestor_name": "설정",
                    "selector": {
                        "automation_id": "",
                        "name": "저장",
                        "control_type": "Button",
                        "parent_name": "왼쪽 영역",
                        "ancestor_name": "설정",
                        "match_mode": "exact",
                    },
                },
                {
                    "name": "저장",
                    "control_type": "Button",
                    "automation_id": "",
                    "parent_name": "오른쪽 영역",
                    "ancestor_name": "설정",
                    "selector": {
                        "automation_id": "",
                        "name": "저장",
                        "control_type": "Button",
                        "parent_name": "오른쪽 영역",
                        "ancestor_name": "설정",
                        "match_mode": "exact",
                    },
                },
            ],
            diagnostic={
                "locator_strategy": "control_type_name_exact",
                "fallback_level": 2,
                "candidate_count": 2,
                "matched_name": "",
                "matched_control_type": "",
                "matched_automation_id": "",
                "search_duration_ms": 1.0,
            },
        )
        plan = [{
            "action": "uia_click",
            "target": "테스트",
            "direction": "저장",
        }]
        payload = {
            "plan": plan,
            "slots": {},
            "failed_step": 1,
            "learning_candidate": {"plan": plan, "name": "저장 클릭"},
        }
        waiting = parser._queue_uia_target_choice(
            ambiguity, payload, "uia-session", "저장을 눌러"
        )
        self.assertEqual("clarification_required", waiting["status"])
        confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
        execution_result = {
            "success": True,
            "verified": False,
            "verification_status": "confirmation_required",
            "verification": [],
        }
        with mock.patch.object(
            parser.action_executor,
            "execute_plan",
            return_value=execution_result,
        ) as execute, mock.patch.object(
            parser.skill_learning_service, "stage_candidate"
        ) as stage:
            completed = parser.resolve_pending_confirmation(
                "uia-session", confirmation_id, "uia_target_2"
            )

        self.assertTrue(completed["success"])
        resumed_plan = execute.call_args.args[0]
        self.assertEqual(
            "오른쪽 영역",
            resumed_plan[0]["selector"]["parent_name"],
        )
        self.assertEqual(1, execute.call_args.kwargs["start_step"])
        self.assertEqual(
            "오른쪽 영역",
            stage.call_args.args[0]["plan"][0]["selector"]["parent_name"],
        )

    def test_learned_uia_choice_resumes_the_same_route(self):
        parser = CommandParser()
        candidates = []
        for parent_name in ("왼쪽 영역", "오른쪽 영역"):
            selector = {
                "automation_id": "",
                "name": "저장",
                "control_type": "Button",
                "parent_name": parent_name,
                "ancestor_name": "설정",
                "match_mode": "exact",
            }
            candidates.append({
                "name": "저장",
                "control_type": "Button",
                "automation_id": "",
                "parent_name": parent_name,
                "ancestor_name": "설정",
                "selector": selector,
            })
        ambiguity = UIAutomationAmbiguousTarget(
            "저장 버튼이 두 개입니다.", candidates=candidates
        )
        ambiguity.failed_step = 1
        ambiguity.skill_execution = {"selected_route": "uia"}
        skill = {
            "state": "active",
            "uia_plan": [{
                "action": "uia_click",
                "target": "테스트",
                "direction": "저장",
            }],
            "execution_profile": {
                "primary_route": "uia",
                "fallback_routes": ["python"],
                "max_fallback_attempts": 1,
            },
            "learning": {"intent": "CLICK_SAVE", "slots": []},
        }
        waiting = parser._queue_learned_uia_target_choice(
            ambiguity,
            app_name="테스트",
            macro_name="저장클릭",
            skill=skill,
            slots={},
            session_id="learned-uia-session",
            original_command="저장을 눌러",
        )
        confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
        execution_result = {
            "success": True,
            "verified": False,
            "verification_status": "confirmation_required",
            "verification": [],
        }
        with mock.patch.object(
            parser.skill_executor, "execute", return_value=execution_result
        ) as execute:
            completed = parser.resolve_pending_confirmation(
                "learned-uia-session", confirmation_id, "uia_target_2"
            )

        self.assertTrue(completed["success"])
        self.assertEqual("learned_macro", completed["action"])
        resumed_skill = execute.call_args.kwargs["skill"]
        self.assertEqual(
            "오른쪽 영역",
            resumed_skill["uia_plan"][0]["selector"]["parent_name"],
        )
        self.assertEqual(
            "uia", resumed_skill["execution_profile"]["primary_route"]
        )
        self.assertEqual([], resumed_skill["execution_profile"]["fallback_routes"])


if __name__ == "__main__":
    unittest.main()
