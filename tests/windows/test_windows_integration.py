"""Windows integration boundaries for built-in commands.

Only the close test performs a real OS action, and it targets a uniquely named
process created by the test itself. Media keys and shutdown commands are mocked.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest import mock

import psutil

from engine.builtins import BuiltinMacros


@unittest.skipUnless(os.name == "nt", "Windows-only integration tests")
class WindowsBuiltinIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.builtins = BuiltinMacros(dict_mgr=mock.Mock(), parser=mock.Mock())

    def test_close_kills_only_the_unique_background_process(self):
        ping = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "ping.exe")
        if not os.path.isfile(ping):
            self.skipTest("Windows ping.exe is unavailable")

        process = None
        with tempfile.TemporaryDirectory(prefix="jarvis-close-test-") as temp_dir:
            copied_exe = os.path.join(temp_dir, f"jarvis_close_{uuid.uuid4().hex[:12]}.exe")
            shutil.copy2(ping, copied_exe)
            process = subprocess.Popen(
                [copied_exe, "-t", "127.0.0.1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                time.sleep(0.2)
                self.assertTrue(psutil.pid_exists(process.pid))

                response = self.builtins.handle_close(
                    "통합 테스트 종료", [], "Jarvis test process", copied_exe, None
                )

                process.wait(timeout=3)
                self.assertFalse(psutil.pid_exists(process.pid))
                self.assertIsInstance(response, dict)
                self.assertTrue(response["success"])
                self.assertTrue(response["message"].strip())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def test_media_commands_emit_expected_virtual_keys(self):
        fake_user32 = mock.Mock()
        fake_windll = mock.Mock(user32=fake_user32)
        with mock.patch("engine.builtins.ctypes.windll", fake_windll):
            self.builtins.handle_playpause()
            self.builtins.handle_vol_up()
            self.builtins.handle_vol_down()
            self.builtins.handle_mute()

        expected = (
            [mock.call(0xB3, 0, 0, 0)]
            + [mock.call(0xAF, 0, 0, 0)] * 5
            + [mock.call(0xAE, 0, 0, 0)] * 5
            + [mock.call(0xAD, 0, 0, 0)]
        )
        self.assertEqual(expected, fake_user32.keybd_event.call_args_list)

    def test_shutdown_and_cancel_use_expected_windows_commands(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch("engine.builtins.subprocess.run", return_value=completed) as run:
            shutdown_response = self.builtins.handle_shutdown()
            cancel_response = self.builtins.handle_cancel_shutdown()

        self.assertEqual(2, run.call_count)
        self.assertEqual(["shutdown.exe", "/s", "/t", "60"], run.call_args_list[0].args[0])
        self.assertEqual(["shutdown.exe", "/a"], run.call_args_list[1].args[0])
        for call in run.call_args_list:
            self.assertFalse(call.kwargs["shell"])
        self.assertTrue(shutdown_response["message"].strip())
        self.assertTrue(cancel_response["message"].strip())
        self.assertTrue(shutdown_response["success"])
        self.assertTrue(cancel_response["success"])
        self.assertTrue(shutdown_response["verified"])
        self.assertTrue(cancel_response["verified"])

    def test_shutdown_failure_is_not_reported_as_success(self):
        completed = subprocess.CompletedProcess(
            [], 5, stdout="", stderr="Access is denied"
        )
        with mock.patch("engine.builtins.subprocess.run", return_value=completed):
            response = self.builtins.handle_shutdown()

        self.assertFalse(response["success"])
        self.assertEqual("execution_error", response["error_type"])
        self.assertEqual(5, response["data"]["returncode"])

    def test_shutdown_process_error_is_not_reported_as_success(self):
        with mock.patch(
            "engine.builtins.subprocess.run",
            side_effect=subprocess.TimeoutExpired("shutdown.exe", 10),
        ):
            response = self.builtins.handle_cancel_shutdown()

        self.assertFalse(response["success"])
        self.assertEqual("environment_error", response["error_type"])


if __name__ == "__main__":
    unittest.main()
