import unittest

from verification import prototype11_stage12_probe as probe


class Prototype11Stage12ProbeSafetyTests(unittest.TestCase):
    def test_failure_injection_is_private_and_never_executes_a_fix(self):
        report = probe.run_probe()
        self.assertTrue(report["success"])
        self.assertTrue(report["failure_injection_only"])
        self.assertFalse(report["user_documents_modified"])
        self.assertFalse(report["user_applications_started"])
        self.assertFalse(report["paths_or_contents_reported"])
        self.assertFalse(report["automatic_fix_executed"])
        self.assertFalse(report["new_executable_built"])
        self.assertTrue(all(report["result"]["checks"].values()))


if __name__ == "__main__":
    unittest.main()
