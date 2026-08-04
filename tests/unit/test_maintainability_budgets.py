"""Source budgets for every engine function and high-risk module."""

import unittest
from pathlib import Path

from verification.maintenance_audit import audit_long_function_budgets

ROOT = Path(__file__).resolve().parents[2]


class MaintainabilityBudgetTests(unittest.TestCase):
    def test_all_long_engine_functions_are_frozen_and_time_bounded(self):
        self.assertEqual([], audit_long_function_budgets(ROOT))


if __name__ == "__main__":
    unittest.main()
