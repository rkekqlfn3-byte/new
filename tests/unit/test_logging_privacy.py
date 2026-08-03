import logging
import unittest
from unittest import mock

from engine.api import command_api
from engine.logging_config import RedactingFormatter, redact_text


class LoggingPrivacyTests(unittest.TestCase):
    def test_redact_text_removes_quoted_keyed_and_unc_paths(self):
        cases = (
            'File "C:\\Users\\Alice\\Private Folder\\document.py", line 7',
            "path=C:\\Users\\Alice\\Private Folder\\급여.xlsx error=denied",
            "읽는 중: C:\\private\\급여.xlsx",
            "읽는 중: C:\\private\\급여,2026;최종.xlsx",
            r"target_path=\\server\private\급여.xlsx status=failed",
        )
        for value in cases:
            with self.subTest(value=value):
                redacted = redact_text(value)
                self.assertIn("[PRIVATE_PATH]", redacted)
                self.assertNotIn("Alice", redacted)
                self.assertNotIn("급여", redacted)
                self.assertNotIn("Private Folder", redacted)

    def test_redaction_preserves_following_structured_diagnostic_fields(self):
        redacted = redact_text(
            "path=C:\\Users\\Alice\\Private Folder\\state.json "
            "error=PermissionError retryable=False"
        )
        self.assertIn("path=[PRIVATE_PATH]", redacted)
        self.assertIn("error=PermissionError", redacted)
        self.assertIn("retryable=False", redacted)

    def test_formatter_redacts_secrets_and_traceback_style_paths(self):
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            "jarvis.test", logging.INFO, __file__, 1,
            'Bearer abcdefghijklmnop File "C:\\Users\\Alice\\secret.py", line 1',
            (), None,
        )
        formatted = formatter.format(record)
        self.assertNotIn("abcdefghijklmnop", formatted)
        self.assertNotIn("Alice", formatted)
        self.assertIn("Bearer [REDACTED]", formatted)
        self.assertIn("[PRIVATE_PATH]", formatted)

    def test_terminal_payload_is_forwarded_to_ui_but_not_persistent_logger(self):
        message = "[Reader] 파일 읽는 중: C:\\private\\급여.xlsx"
        callback = mock.Mock()
        with (
            mock.patch.object(command_api.logger, "info") as logger_info,
            mock.patch.object(
                command_api.eel,
                "log_terminal",
                return_value=callback,
                create=True,
            ) as ui_log,
        ):
            command_api._log_to_terminal(message)

        logger_info.assert_called_once_with("Terminal message forwarded")
        ui_log.assert_called_once_with(message)
        callback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
