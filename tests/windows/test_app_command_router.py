"""Tests for the common native application command router."""

import unittest
from types import SimpleNamespace
from unittest import mock

from engine.app_actions import AppActionRegistry, AppCommandRouter, PreparedAction
from engine.decision import DecisionOutcome


def prepared_action(app="excel", operation="write_cell", target="B1"):
    return PreparedAction(
        app=app,
        operation=operation,
        document_id="doc-1",
        workbook_name="Book1",
        sheet="Sheet1",
        target=target,
        params={"cell": target, "value": 100},
        current_state={"value": None},
        estimated_changes=1,
        destructive=False,
        reversible=True,
        verification_method="cell_equals",
        context_fingerprint="fingerprint-1",
        prepared_at="2026-07-14T00:00:00+09:00",
    )


class FakeAdapter:
    def __init__(self, prepared=None, changed=True):
        self.prepared = prepared or prepared_action()
        self.changed = changed
        self.prepare_calls = []
        self.execute_calls = []

    def prepare(self, operation, params):
        self.prepare_calls.append((operation, params))
        return self.prepared

    def execute(self, prepared):
        self.execute_calls.append(prepared)
        return {"changed": self.changed, "verified": True}


class AppCommandRouterTests(unittest.TestCase):
    @staticmethod
    def owner(adapter):
        owner = SimpleNamespace(
            app_action_registry=AppActionRegistry({"excel": adapter}),
            decision_engine=mock.Mock(),
            preference_manager=mock.Mock(),
        )
        owner._queue_prepared_action_confirmation = mock.Mock()
        owner._queue_app_target_choice = mock.Mock()
        owner._queue_changed_app_context = mock.Mock()
        owner._queue_app_method_choice = mock.Mock()
        return owner

    def test_prepare_selects_alias_and_uses_replaced_registry(self):
        first = FakeAdapter()
        owner = self.owner(first)
        router = AppCommandRouter(owner)

        prepared = router.prepare({
            "target": "엑셀",
            "operation": "write_cell",
            "params": {"cell": "B1", "value": 100},
        })
        second = FakeAdapter(prepared_action(target="C1"))
        owner.app_action_registry = AppActionRegistry({"excel": second})
        replaced = router.prepare("excel", "write_cell", {"cell": "C1", "value": 200})

        self.assertEqual("B1", prepared.target)
        self.assertEqual("C1", replaced.target)
        self.assertEqual(1, len(first.prepare_calls))
        self.assertEqual(1, len(second.prepare_calls))

    def test_execute_runs_prepare_decision_adapter_and_result_conversion(self):
        adapter = FakeAdapter()
        owner = self.owner(adapter)
        owner.decision_engine.evaluate.return_value = DecisionOutcome(
            decision="execute", reason="safe_prepared_app_action"
        )
        router = AppCommandRouter(owner)
        logs = []

        result = router.execute(
            {"target": "excel", "operation": "write_cell", "params": {}},
            "session-a",
            "엑셀 B1에 100 입력",
            log_callback=logs.append,
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("app_command", result["action"])
        self.assertEqual(1, len(adapter.execute_calls))
        self.assertEqual(1, len(logs))
        self.assertIn("실행 및 검증", logs[0])

    def test_execute_delegates_confirmation_without_running_adapter(self):
        adapter = FakeAdapter()
        owner = self.owner(adapter)
        decision = DecisionOutcome(
            decision="confirmation_required",
            reason="destructive_action",
            requires_confirmation=True,
        )
        owner.decision_engine.evaluate.return_value = decision
        owner._queue_prepared_action_confirmation.return_value = {
            "status": "confirmation_required"
        }
        router = AppCommandRouter(owner)
        request = {"target": "excel", "operation": "write_cell", "params": {}}

        result = router.execute(request, "session-b", "원본 명령")

        self.assertEqual("confirmation_required", result["status"])
        self.assertEqual([], adapter.execute_calls)
        owner._queue_prepared_action_confirmation.assert_called_once_with(
            adapter.prepared, request, decision, "session-b", "원본 명령"
        )

    def test_unsupported_app_returns_common_blocked_result(self):
        adapter = FakeAdapter()
        owner = self.owner(adapter)
        router = AppCommandRouter(owner)

        result = router.execute(
            {"target": "unknown-app", "operation": "write", "params": {}},
            "session-c",
            "지원하지 않는 앱 명령",
        )

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("validation_error", result["error_type"])
        self.assertIn("지원하지 않는 네이티브 앱", result["message"])


if __name__ == "__main__":
    unittest.main()
