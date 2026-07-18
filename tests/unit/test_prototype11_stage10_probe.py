import unittest
from unittest import mock

from verification import prototype11_stage10_probe as probe


class Prototype11Stage10ProbeSafetyTests(unittest.TestCase):
    def test_existing_user_office_process_skips_owned_fixture_creation(self):
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            report = probe.run_probe()

        self.assertFalse(report["success"])
        self.assertEqual("skipped_user_office_running", report["result"]["status"])
        self.assertTrue(report["result"]["user_process_protected"])
        context.assert_not_called()

    def test_only_full_owned_fixture_pass_claims_success(self):
        with (
            mock.patch.object(probe, "_process_ids", side_effect=[set(), set()]),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {"status": "passed"}
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertFalse(report["user_documents_modified"])
        self.assertFalse(report["paths_or_contents_reported"])

    def test_both_report_format_is_preserved_in_probe_contract(self):
        with (
            mock.patch.object(probe, "_process_ids", side_effect=[set(), set()]),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            worker = context.return_value.Process.return_value
            worker.is_alive.return_value = False
            output = context.return_value.Queue.return_value
            output.get.return_value = {"status": "passed"}
            report = probe.run_probe(report_format="both")

        self.assertTrue(report["success"])
        self.assertEqual("both", report["report_format"])


if __name__ == "__main__":
    unittest.main()
