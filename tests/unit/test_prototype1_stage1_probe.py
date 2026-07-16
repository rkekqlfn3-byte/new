import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verification.prototype1_stage1_probe import (
    extract_server_executable,
    executable_bits,
    live_checks_passed,
    run_live_probe,
)


class Stage1ProbeTests(unittest.TestCase):
    def test_extract_server_executable_handles_quoted_command(self):
        self.assertEqual(
            r"C:\Program Files\Microsoft Office\EXCEL.EXE",
            extract_server_executable(
                r'"C:\Program Files\Microsoft Office\EXCEL.EXE" /automation'
            ),
        )

    def test_extract_server_executable_handles_unquoted_command(self):
        self.assertEqual(
            r"C:\Program Files\Microsoft Office\WINWORD.EXE",
            extract_server_executable(
                r"C:\Program Files\Microsoft Office\WINWORD.EXE /Automation"
            ),
        )

    def test_live_checks_require_every_explicit_capability(self):
        complete = {
            "dedicated_instance": True,
            "selection_read": True,
            "temporary_edit_verified": True,
            "undo_verified": True,
            "save_reopen_verified": True,
            "process_cleanup_verified": True,
        }
        self.assertTrue(live_checks_passed("excel", complete))
        complete["undo_verified"] = False
        self.assertFalse(live_checks_passed("excel", complete))

    def test_executable_bits_reads_pe_machine_type(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "office.exe"
            payload = bytearray(256)
            payload[0:2] = b"MZ"
            payload[0x3C:0x40] = (128).to_bytes(4, "little")
            payload[128:132] = b"PE\0\0"
            payload[132:134] = (0x8664).to_bytes(2, "little")
            path.write_bytes(payload)
            self.assertEqual(64, executable_bits(str(path)))

    @patch("verification.prototype1_stage1_probe._process_ids")
    def test_live_probe_refuses_to_touch_running_user_app(self, process_ids):
        process_ids.return_value = {4312}
        result = run_live_probe("word", {"registered": True})
        self.assertEqual("skipped_user_processes_running", result["status"])
        self.assertEqual([4312], result["baseline_pids"])

    def test_live_probe_reports_unregistered_application(self):
        result = run_live_probe("powerpoint", {"registered": False})
        self.assertEqual("unavailable", result["status"])


if __name__ == "__main__":
    unittest.main()
