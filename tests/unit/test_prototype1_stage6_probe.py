import unittest
from unittest import mock

from verification import prototype1_stage6_probe as probe


class PrototypeStage6ProbeSafetyTests(unittest.TestCase):
    def test_existing_user_process_skips_owned_fixture_creation(self):
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            result = probe._run_isolated("word")

        self.assertEqual("skipped_user_processes_running", result["status"])
        self.assertTrue(result["user_process_protected"])
        context.assert_not_called()

    def test_report_never_contains_user_paths_or_text(self):
        with mock.patch.object(
            probe,
            "_run_isolated",
            return_value={"status": "passed", "owned_fixture_only": True},
        ):
            report = probe.run_probe(("word", "powerpoint"))

        self.assertTrue(report["success"])
        self.assertFalse(report["user_documents_modified"])
        self.assertFalse(report["paths_or_text_reported"])


if __name__ == "__main__":
    unittest.main()
