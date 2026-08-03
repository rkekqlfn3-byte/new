"""Isolated failure injections for confirmation and COM recovery boundaries."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine.confirmation import ConfirmationDispatcher, ConfirmationResponseHandler
from engine.edit_mode.native_bridge import NativeDocumentBridge, NativeOfficeBusy
from engine.execution_runtime import ExecutionController
from engine.managers.pending_confirmation_manager import PendingConfirmationManager


def _options():
    return [
        {"id": "run", "label": "실행", "aliases": ["예"]},
        {"id": "cancel", "label": "취소", "cancel": True},
    ]


class ConfirmationFailureInjectionTests(unittest.TestCase):
    def test_expired_confirmation_drops_paused_execution_without_running(self):
        now = [1_700_000_000.0]
        manager = PendingConfirmationManager(
            ttl_seconds=1,
            clock=lambda: now[0],
        )
        with tempfile.TemporaryDirectory(prefix="jarvis-confirm-expire-") as temp_dir:
            controller = ExecutionController(Path(temp_dir) / "diagnostics.json")
            execution_id = controller.begin("dangerous action")
            record = manager.create(
                session_id="expire-session",
                execution_id=execution_id,
                original_command="위험 작업",
                reason="confirmation_demo",
                message="실행할까요?",
                options=_options(),
                payload={"kind": "danger"},
            )
            controller.pause_for_confirmation(record["confirmation_id"])
            handler = ConfirmationResponseHandler(manager, controller)
            now[0] += 2

            resolution = handler.resolve_selection(
                "expire-session",
                confirmation_id=record["confirmation_id"],
                option_id="run",
            )
            diagnostics = controller.diagnostics()

        self.assertEqual("confirmation_expired", resolution.result["status"])
        self.assertFalse(resolution.result["success"])
        self.assertEqual([], diagnostics["pending"])
        self.assertIsNone(diagnostics["current"])
        self.assertEqual([], diagnostics["records"])

    def test_cancelled_confirmation_never_dispatches_domain_handler(self):
        manager = PendingConfirmationManager()
        controller = mock.Mock()
        record = manager.create(
            session_id="cancel-session",
            execution_id="",
            original_command="위험 작업",
            reason="confirmation_demo",
            message="실행할까요?",
            options=_options(),
            payload={"kind": "danger"},
        )
        domain_handler = mock.Mock(return_value={"success": True})
        dispatcher = ConfirmationDispatcher(
            ConfirmationResponseHandler(manager, controller),
            handlers={"danger": domain_handler},
        )
        runtime = SimpleNamespace(
            edit_mode_controller=SimpleNamespace(cancel_pending_edit=None)
        )

        result = dispatcher.resolve(
            runtime,
            "cancel-session",
            confirmation_id=record["confirmation_id"],
            option_id="cancel",
        )

        self.assertFalse(result["success"])
        self.assertEqual("user_cancelled", result["error_type"])
        domain_handler.assert_not_called()


class ComFailureInjectionTests(unittest.TestCase):
    def test_persistent_com_busy_exits_at_bounded_deadline(self):
        bridge = NativeDocumentBridge()
        busy = NativeOfficeBusy("Excel busy")
        with (
            mock.patch.object(bridge, "find_document", side_effect=busy) as find,
            mock.patch(
                "engine.edit_mode.native_bridge.time.monotonic",
                side_effect=[0.0, 0.0, 0.2],
            ),
            mock.patch("engine.edit_mode.native_bridge.time.sleep"),
        ):
            with self.assertRaises(NativeOfficeBusy) as raised:
                bridge.wait_for_document(
                    "excel", r"C:\owned\fixture.xlsx", timeout=0.1
                )

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(1, find.call_count)

    def test_transient_com_busy_recovers_only_to_exact_document(self):
        bridge = NativeDocumentBridge()
        expected = {
            "app_type": "excel",
            "file_path": r"C:\owned\fixture.xlsx",
            "window_handle": 101,
        }
        with (
            mock.patch.object(
                bridge,
                "find_document",
                side_effect=[NativeOfficeBusy("retry"), expected],
            ) as find,
            mock.patch(
                "engine.edit_mode.native_bridge.time.monotonic",
                side_effect=[0.0, 0.0, 0.01],
            ),
            mock.patch("engine.edit_mode.native_bridge.time.sleep"),
        ):
            found = bridge.wait_for_document(
                "excel", expected["file_path"], timeout=0.1
            )

        self.assertEqual(expected, found)
        self.assertEqual(2, find.call_count)


if __name__ == "__main__":
    unittest.main()
