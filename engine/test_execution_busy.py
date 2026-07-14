"""Concurrent-command protection for the single ExecutionController.

A single CommandParser instance can be entered from several eel callback
threads. begin() must reject a second command while one is running instead of
overwriting its diagnostics or clearing its pending cancel signal — while still
allowing a fresh command once the running one pauses for confirmation or ends.
"""

import os
import tempfile
import threading
import unittest

from engine.execution_result import normalize_error_type
from engine.execution_runtime import (
    ExecutionBusyError,
    ExecutionCancelled,
    ExecutionController,
)


class ExecutionBusyGuardTests(unittest.TestCase):
    def _controller(self, temp_dir):
        return ExecutionController(os.path.join(temp_dir, "diagnostics.json"))

    def test_second_begin_while_running_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-busy-") as temp_dir:
            controller = self._controller(temp_dir)
            controller.begin("first")
            with self.assertRaises(ExecutionBusyError):
                controller.begin("second")

    def test_begin_allowed_after_finish(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-busy-") as temp_dir:
            controller = self._controller(temp_dir)
            controller.begin("first")
            controller.finish(True, response="done")
            # No exception: the controller is free again.
            second_id = controller.begin("second")
            self.assertTrue(second_id)

    def test_begin_allowed_while_previous_is_paused_for_confirmation(self):
        # Protects the existing confirmation flow: a paused command frees
        # ``current`` so unrelated work may run before the user responds.
        with tempfile.TemporaryDirectory(prefix="jarvis-busy-") as temp_dir:
            controller = self._controller(temp_dir)
            first_id = controller.begin("first")
            controller.pause_for_confirmation("confirm-a")
            second_id = controller.begin("second")
            self.assertNotEqual(first_id, second_id)
            controller.finish(True, response="second done")
            self.assertTrue(controller.resume(first_id))

    def test_rejected_begin_preserves_running_cancel_signal(self):
        # The core safety property: a rejected second command must not clear the
        # cancel event that a cancel() already set for the running command.
        with tempfile.TemporaryDirectory(prefix="jarvis-busy-") as temp_dir:
            controller = self._controller(temp_dir)
            controller.begin("first")
            self.assertTrue(controller.cancel())
            with self.assertRaises(ExecutionBusyError):
                controller.begin("second")
            with self.assertRaises(ExecutionCancelled):
                controller.check_cancelled()

    def test_busy_error_exposes_explicit_retryable_metadata(self):
        error = ExecutionBusyError("busy")
        self.assertEqual("busy", error.error_type)
        self.assertEqual("busy", error.status)
        self.assertTrue(error.retryable)
        # "busy" is a first-class canonical error type, not coerced to unknown.
        self.assertEqual("busy", normalize_error_type("busy"))

    def test_concurrent_begins_admit_exactly_one(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-busy-") as temp_dir:
            controller = self._controller(temp_dir)
            threads_count = 8
            barrier = threading.Barrier(threads_count)
            admitted = []
            rejected = []
            lock = threading.Lock()

            def worker(index):
                barrier.wait()
                try:
                    controller.begin(f"cmd-{index}")
                except ExecutionBusyError:
                    with lock:
                        rejected.append(index)
                else:
                    with lock:
                        admitted.append(index)

            threads = [
                threading.Thread(target=worker, args=(i,))
                for i in range(threads_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)

            self.assertEqual(1, len(admitted), "정확히 하나만 실행을 시작해야 합니다.")
            self.assertEqual(threads_count - 1, len(rejected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
