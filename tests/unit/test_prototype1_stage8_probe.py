import unittest
from unittest import mock

from verification import prototype1_stage8_probe as probe


class PrototypeStage8ProbeSafetyTests(unittest.TestCase):
    def test_existing_user_process_skips_owned_fixture_creation(self):
        with (
            mock.patch.object(probe, "_process_ids", return_value={1234}),
            mock.patch.object(probe.multiprocessing, "get_context") as context,
        ):
            result = probe._run_isolated("excel")

        self.assertEqual("skipped_user_processes_running", result["status"])
        self.assertTrue(result["user_process_protected"])
        context.assert_not_called()

    def test_all_four_passed_is_required_for_prototype_ready(self):
        with mock.patch.object(
            probe,
            "_run_isolated",
            return_value={"status": "passed", "owned_fixture_only": True},
        ):
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertTrue(report["prototype_1_0_ready"])
        self.assertFalse(report["user_documents_modified"])
        self.assertFalse(report["paths_or_text_reported"])

    def test_skipped_app_never_claims_prototype_ready(self):
        def result(app_type):
            if app_type == "hwp":
                return {"status": "skipped_user_processes_running"}
            return {"status": "passed"}

        with mock.patch.object(probe, "_run_isolated", side_effect=result):
            report = probe.run_probe()

        self.assertTrue(report["success"])
        self.assertFalse(report["prototype_1_0_ready"])


if __name__ == "__main__":
    unittest.main()
