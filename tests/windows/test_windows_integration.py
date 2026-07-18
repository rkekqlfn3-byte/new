"""Windows integration boundaries for built-in commands.

Only the close test performs a real OS action, and it targets a uniquely named
process created by the test itself. Media keys, Core Audio, and shutdown commands
are mocked.
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
from engine.system_volume import adjust_system_volume, set_system_volume


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
        with mock.patch("engine.builtins.ctypes.windll", fake_windll), \
             mock.patch(
                 "engine.builtins.adjust_system_volume",
                 side_effect=[(40, 50), (50, 40)],
             ) as adjust_volume, \
             mock.patch(
                 "engine.builtins.set_system_muted", return_value=True
             ) as set_muted:
            self.builtins.handle_playpause()
            volume_up = self.builtins.handle_vol_up("소리 키워")
            volume_down = self.builtins.handle_vol_down("소리 줄여")
            muted = self.builtins.handle_mute("음소거")

        self.assertEqual(
            [mock.call(0xB3, 0, 0, 0)],
            fake_user32.keybd_event.call_args_list,
        )
        self.assertEqual([mock.call(10), mock.call(-10)], adjust_volume.call_args_list)
        self.assertTrue(volume_up["verified"])
        self.assertTrue(volume_down["verified"])
        set_muted.assert_called_once_with(True)
        self.assertTrue(muted["success"])
        self.assertTrue(muted["verified"])

    def test_core_audio_reuses_an_existing_com_apartment(self):
        endpoint = mock.Mock()
        endpoint.GetMasterVolumeLevelScalar.return_value = 0.3
        changed_mode = OSError(-2147417850, "COM apartment already initialized")
        with mock.patch(
            "engine.system_volume.CoInitialize", side_effect=changed_mode
        ), mock.patch(
            "engine.system_volume.CoUninitialize"
        ) as uninitialize, mock.patch(
            "engine.system_volume._endpoint_volume", return_value=endpoint
        ):
            applied = set_system_volume(30)

        self.assertEqual(30, applied)
        endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(0.3, None)
        endpoint.SetMute.assert_called_once_with(False, None)
        uninitialize.assert_not_called()

    def test_core_audio_relative_adjustment_is_clamped_and_verified(self):
        endpoint = mock.Mock()
        endpoint.GetMasterVolumeLevelScalar.side_effect = [0.95, 1.0]
        with mock.patch("engine.system_volume.CoInitialize"), mock.patch(
            "engine.system_volume.CoUninitialize"
        ), mock.patch(
            "engine.system_volume._endpoint_volume", return_value=endpoint
        ):
            before, applied = adjust_system_volume(10)

        self.assertEqual((95, 100), (before, applied))
        endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(1.0, None)
        endpoint.SetMute.assert_called_once_with(False, None)

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
