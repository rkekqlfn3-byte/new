import unittest

from verification.utterance_acceptance_battery import build_cases, run_battery


class UtteranceAcceptanceBatteryTests(unittest.TestCase):
    def test_corpus_has_exact_goal_distribution_without_duplicate_ids(self):
        cases = build_cases()
        self.assertEqual(1000, len(cases))
        self.assertEqual(1000, len({case.case_id for case in cases}))
        # Repeating the same natural phrase under a different selected range is
        # intentional: context binding, not wording novelty, is under test.
        self.assertGreaterEqual(
            len({(case.category, case.text) for case in cases}), 850
        )

    def test_all_cases_pass_without_ai_apps_or_user_data(self):
        report = run_battery()
        self.assertTrue(report["success"], report["failures"][:10])
        self.assertEqual(0, report["outcomes"].get("crash", 0))
        self.assertEqual(0, report["outcomes"].get("wrong_action", 0))
        self.assertTrue(report["checks"]["risk_safe_block_100_percent"])
        self.assertTrue(report["checks"]["failure_records_content_free"])


if __name__ == "__main__":
    unittest.main()
