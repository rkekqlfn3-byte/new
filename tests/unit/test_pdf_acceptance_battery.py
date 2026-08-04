import json
import unittest

from verification.pdf_acceptance_battery import build_cases, run_battery


class PdfAcceptanceBatteryTests(unittest.TestCase):
    def test_corpus_has_120_unique_meaningful_cases_in_twelve_categories(self):
        cases = build_cases()

        self.assertEqual(120, len(cases))
        self.assertEqual(120, len({case.case_id for case in cases}))
        self.assertEqual(120, len({case.text for case in cases}))
        categories = {case.category for case in cases}
        self.assertEqual(12, len(categories))
        self.assertTrue(
            all(sum(case.category == category for case in cases) == 10 for category in categories)
        )

    def test_all_pdf_cases_are_safe_and_correct_without_external_ai(self):
        report = run_battery()

        self.assertTrue(report["success"], report["failures"])
        self.assertTrue(report["result"]["checks"]["intent_accuracy_100_percent"])
        self.assertTrue(report["result"]["checks"]["unapproved_write_zero"])
        self.assertTrue(report["result"]["checks"]["external_ai_calls_zero"])
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("이 PDF는 총 몇 페이지야", encoded)
        self.assertNotIn("jarvis-pdf-acceptance-", encoded)


if __name__ == "__main__":
    unittest.main()
