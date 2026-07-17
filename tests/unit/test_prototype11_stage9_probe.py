import unittest
from unittest import mock

from verification import prototype11_stage9_probe as probe


class Prototype11Stage9ProbeSafetyTests(unittest.TestCase):
    def test_existing_user_excel_process_skips_owned_fixture_creation(self):
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            report = probe.run_probe()

        self.assertFalse(report["success"])
        self.assertEqual("skipped_user_excel_running", report["result"]["status"])
        self.assertTrue(report["result"]["user_process_protected"])
        context.assert_not_called()

    def test_only_a_full_owned_fixture_pass_claims_success(self):
        with (
            mock.patch.object(
                probe, "_process_ids", side_effect=[set(), set(), set(), set()]
            ),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {"status": "passed"}
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertFalse(report["user_documents_modified"])
        self.assertFalse(report["paths_or_code_reported"])
        self.assertFalse(report["security_setting_changed"])
        self.assertTrue(report["result"]["owned_process_cleanup_verified"])
        self.assertEqual(
            "graceful", report["result"]["owned_process_cleanup_mode"]
        )

    def test_trust_block_is_reported_without_claiming_success_or_setting_change(self):
        with (
            mock.patch.object(
                probe, "_process_ids", side_effect=[set(), set(), set(), set()]
            ),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {
                "status": "blocked_vba_trust",
                "security_setting_changed": False,
                "required_user_action": "enable manually",
            }
            report = probe.run_probe()

        self.assertFalse(report["success"])
        self.assertEqual("blocked_vba_trust", report["result"]["status"])
        self.assertFalse(report["security_setting_changed"])

    def test_only_created_excel_process_is_force_cleaned_after_graceful_timeout(self):
        with (
            mock.patch.object(
                probe, "_process_ids", side_effect=[set(), {4321}, set()]
            ),
            mock.patch.object(probe, "_wait_for_cleanup", return_value=False),
            mock.patch.object(probe, "_stop_created_processes") as stop_created,
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {"status": "passed"}
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertTrue(report["result"]["owned_process_cleanup_verified"])
        self.assertEqual(
            "forced_owned_instance",
            report["result"]["owned_process_cleanup_mode"],
        )
        stop_created.assert_called_once_with(set())


if __name__ == "__main__":
    unittest.main()
