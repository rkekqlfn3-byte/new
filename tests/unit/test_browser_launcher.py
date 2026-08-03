import unittest
from unittest import mock

import jarvis_app
from engine.browser_launcher import (
    BrowserAttempt,
    BrowserLauncher,
    BrowserLaunchError,
    BrowserLaunchResult,
    format_browser_failure,
)


class RecordingProcessLauncher:
    def __init__(self, failing_paths=()):
        self.failing_paths = set(failing_paths)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if command[0] in self.failing_paths:
            raise OSError("launch blocked")
        return object()


class BrowserFallbackTests(unittest.TestCase):
    def test_missing_chrome_uses_edge(self):
        processes = RecordingProcessLauncher()
        launcher = BrowserLauncher(
            chrome_finder=lambda: None,
            edge_finder=lambda: "C:\\Edge\\msedge.exe",
            additional_finder=lambda: [],
            process_launcher=processes,
            default_opener=lambda *_args, **_kwargs: False,
        )

        with self.assertLogs("engine.browser_launcher", level="INFO") as logs:
            result = launcher.launch("http://127.0.0.1:8080/index.html")

        self.assertEqual("Microsoft Edge", result.selected_browser)
        self.assertEqual("C:\\Edge\\msedge.exe", processes.calls[0][0][0])
        self.assertIn("Google Chrome", "\n".join(logs.output))
        self.assertIn("Microsoft Edge", "\n".join(logs.output))

    def test_chrome_launch_failure_falls_back_to_edge(self):
        processes = RecordingProcessLauncher({"C:\\Chrome\\chrome.exe"})
        launcher = BrowserLauncher(
            chrome_finder=lambda: "C:\\Chrome\\chrome.exe",
            edge_finder=lambda: "C:\\Edge\\msedge.exe",
            additional_finder=lambda: [],
            process_launcher=processes,
            default_opener=lambda *_args, **_kwargs: False,
        )

        result = launcher.launch("http://127.0.0.1:8080/index.html")

        self.assertEqual("Microsoft Edge", result.selected_browser)
        self.assertEqual(2, len(processes.calls))
        self.assertIn("실행 실패", result.attempts[0].reason)

    def test_system_default_is_used_after_chrome_and_edge(self):
        opened = []
        launcher = BrowserLauncher(
            chrome_finder=lambda: None,
            edge_finder=lambda: None,
            additional_finder=lambda: [],
            process_launcher=RecordingProcessLauncher(),
            default_opener=lambda url, **_kwargs: opened.append(url) or True,
        )

        result = launcher.launch("http://127.0.0.1:8080/index.html")

        self.assertEqual("시스템 기본 브라우저", result.selected_browser)
        self.assertEqual(["http://127.0.0.1:8080/index.html"], opened)

    def test_all_failures_return_actionable_error_message(self):
        launcher = BrowserLauncher(
            chrome_finder=lambda: None,
            edge_finder=lambda: None,
            additional_finder=lambda: [],
            process_launcher=RecordingProcessLauncher(),
            default_opener=lambda *_args, **_kwargs: False,
        )

        result = launcher.launch("http://127.0.0.1:8080/index.html")
        message = format_browser_failure(result, "C:\\Jarvis\\jarvis.log")

        self.assertFalse(result.success)
        self.assertIn("Chrome", message)
        self.assertIn("Microsoft Edge", message)
        self.assertIn("기본 앱", message)
        self.assertIn("해결 방법", message)
        self.assertIn("C:\\Jarvis\\jarvis.log", message)

    def test_startup_displays_failure_before_aborting(self):
        launcher = mock.Mock()
        launcher.launch.return_value = BrowserLaunchResult(
            None,
            (BrowserAttempt("Google Chrome", False, "실행 실패"),),
        )
        with mock.patch.dict(
            "os.environ", {"JARVIS_BROWSER_MODE": "chrome"}, clear=False
        ), mock.patch.object(
            jarvis_app.eel, "start"
        ) as eel_start, mock.patch.object(
            jarvis_app, "_wait_for_gui_server", return_value=True
        ), mock.patch.object(
            jarvis_app, "show_browser_failure"
        ) as show_error:
            with self.assertRaises(BrowserLaunchError):
                jarvis_app._start_gui(
                    54321, "C:\\Jarvis\\jarvis.log", launcher=launcher
                )

        eel_start.assert_called_once()
        self.assertIsNone(eel_start.call_args.kwargs["mode"])
        self.assertFalse(eel_start.call_args.kwargs["block"])
        show_error.assert_called_once()
        self.assertIn("해결 방법", show_error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
