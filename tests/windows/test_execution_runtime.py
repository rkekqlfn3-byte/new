import glob
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from engine.action_executor import ActionExecutor
from engine.execution_runtime import ExecutionCancelled, ExecutionController
from engine.macro_runner import MacroExecutionError, MacroRunner, MacroTimeoutError


class ExecutionControllerTests(unittest.TestCase):
    def _controller(self, temp_dir):
        return ExecutionController(os.path.join(temp_dir, "diagnostics.json"))

    def test_structured_diagnostic_is_persisted(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = self._controller(temp_dir)
            execution_id = controller.begin("테스트", {"mode": "command"})
            controller.event("wait", "success", {"step": 1})
            record = controller.finish(True, response="완료")
            reloaded = self._controller(temp_dir)
        self.assertEqual(execution_id, record["execution_id"])
        self.assertEqual("success", record["status"])
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(execution_id, reloaded.records[-1]["execution_id"])

    def test_event_observer_reads_existing_events_without_changing_record(self):
        observed = []
        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = ExecutionController(
                os.path.join(temp_dir, "diagnostics.json"),
                event_observer=observed.append,
            )
            execution_id = controller.begin("민감한 사용자 원문")
            controller.event("command_api", "started", {"mode": "edit"})
            record = controller.finish(True)

        self.assertEqual(1, len(observed))
        self.assertEqual(execution_id, observed[0]["execution_id"])
        self.assertEqual("command_api", observed[0]["action"])
        self.assertNotIn("민감한 사용자 원문", str(observed[0]))
        self.assertEqual("started", record["events"][0]["status"])

    def test_event_observer_failure_never_changes_execution(self):
        def broken_observer(_event):
            raise RuntimeError("display unavailable")

        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = ExecutionController(
                os.path.join(temp_dir, "diagnostics.json"),
                event_observer=broken_observer,
            )
            controller.begin("test")
            controller.event("command_api", "started")
            record = controller.finish(True)

        self.assertTrue(record["success"])

    def test_diagnostic_record_carries_app_version(self):
        from engine.version import APP_VERSION
        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = self._controller(temp_dir)
            controller.begin("버전")
            record = controller.finish(True)
        self.assertEqual(APP_VERSION, record["app_version"])

    def test_interruptible_wait_stops_after_cancel(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = self._controller(temp_dir)
            controller.begin("wait")
            errors = []
            worker = threading.Thread(
                target=lambda: self._capture_error(errors, controller.wait, 5), daemon=True
            )
            worker.start()
            time.sleep(0.05)
            self.assertTrue(controller.cancel())
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(errors[0], ExecutionCancelled)

    @staticmethod
    def _capture_error(errors, callback, *args):
        try:
            callback(*args)
        except Exception as error:
            errors.append(error)


class MacroRunnerTests(unittest.TestCase):
    def test_unique_temp_macro_captures_output_and_is_removed(self):
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "jarvis-macro-*")))
        result = MacroRunner(timeout=10).run(
            "import sys\nprint('VALUE=' + sys.argv[1])", "abc"
        )
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), "jarvis-macro-*")))
        self.assertIn("VALUE=abc", result["stdout"])
        self.assertFalse(result["verified"])
        self.assertEqual("confirmation_required", result["verification_status"])
        self.assertEqual(before, after)

    def test_large_stdout_and_stderr_do_not_fill_a_pipe(self):
        result = MacroRunner(timeout=10).run(
            "import sys\n"
            "sys.stdout.write('A' * 1000000 + 'STDOUT-END')\n"
            "sys.stderr.write('B' * 1000000 + 'STDERR-END')\n"
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["stdout"].endswith("STDOUT-END"))
        self.assertTrue(result["stderr"].endswith("STDERR-END"))
        self.assertLessEqual(len(result["stdout"]), 4000)
        self.assertLessEqual(len(result["stderr"]), 4000)

    def test_failure_collects_return_code_and_stderr(self):
        with self.assertRaises(MacroExecutionError) as raised:
            MacroRunner(timeout=10).run(
                "import sys\nsys.stderr.write('broken')\nraise SystemExit(7)"
            )
        self.assertEqual(7, raised.exception.returncode)
        self.assertIn("broken", raised.exception.stderr)

    def test_timeout_terminates_process(self):
        with self.assertRaises(MacroTimeoutError):
            MacroRunner(timeout=0.1).run("import time\ntime.sleep(5)")

    def test_cancel_terminates_process(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-runtime-test-") as temp_dir:
            controller = ExecutionController(os.path.join(temp_dir, "diagnostics.json"))
            controller.begin("macro")
            errors = []
            runner = MacroRunner(controller, timeout=10)
            worker = threading.Thread(
                target=lambda: ExecutionControllerTests._capture_error(
                    errors, runner.run, "import time\ntime.sleep(5)"
                ), daemon=True,
            )
            worker.start()
            deadline = time.monotonic() + 10
            running_seen = False
            while time.monotonic() < deadline and worker.is_alive():
                with controller._lock:
                    running_seen = any(
                        event.get("action") == "python_macro"
                        and event.get("status") == "running"
                        for event in (controller.current or {}).get("events", [])
                    )
                if running_seen:
                    break
                time.sleep(0.01)
            self.assertTrue(running_seen)
            self.assertTrue(controller.cancel())
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.assertIsInstance(errors[0], ExecutionCancelled)


class ActionRetryTests(unittest.TestCase):
    def test_retryable_step_retries_once_and_returns_structured_result(self):
        executor = ActionExecutor({})
        with mock.patch.object(
            executor, "_execute_rendered_step", side_effect=[RuntimeError("once"), None]
        ) as execute, mock.patch.object(
            executor, "_verify_step", return_value={"status": "verified", "reason": "ok"}
        ):
            result = executor.execute_plan(
                [{"action": "clipboard_get"}], retry_attempts=1
            )
        self.assertEqual(2, execute.call_count)
        self.assertEqual("success", result["status"])
        self.assertEqual("action_plan", result["action"])
        self.assertIn("duration_ms", result)

    def test_normal_execution_uses_policy_without_explicit_retry_argument(self):
        executor = ActionExecutor({})
        with mock.patch.object(
            executor, "_execute_rendered_step", side_effect=[OSError("busy"), None]
        ) as execute, mock.patch.object(
            executor, "_verify_step", return_value={"status": "verified", "reason": "ok"}
        ):
            executor.execute_plan([{"action": "clipboard_get"}])
        self.assertEqual(2, execute.call_count)

    def test_side_effecting_action_is_never_automatically_retried(self):
        executor = ActionExecutor({})
        with mock.patch.object(
            executor, "_execute_rendered_step", side_effect=OSError("blocked")
        ) as execute:
            with self.assertRaises(OSError):
                executor.execute_plan([{
                    "action": "type_text", "text": "중복되면 안 됨"
                }], retry_attempts=2)
        self.assertEqual(1, execute.call_count)

    def test_start_step_skips_prior_steps(self):
        executor = ActionExecutor({})
        with mock.patch.object(executor, "_execute_rendered_step") as execute, \
             mock.patch.object(
                 executor, "_verify_step", return_value={"status": "verified", "reason": "ok"}
             ):
            result = executor.execute_plan([
                {"action": "wait", "seconds": 0},
                {"action": "wait", "seconds": 0},
                {"action": "wait", "seconds": 0},
            ], start_step=2)
        self.assertEqual(2, execute.call_count)
        self.assertEqual(2, result["start_step"])


if __name__ == "__main__":
    unittest.main()
