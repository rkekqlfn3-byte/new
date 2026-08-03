"""Source budgets for high-risk workflow and native-edit boundaries."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_FUNCTION_LINES = 99


def maintained_sources():
    paths = [
        ROOT / "engine" / "workflows" / "business_workflow.py",
        ROOT / "engine" / "edit_mode" / "stage10.py",
        ROOT / "engine" / "edit_mode" / "controller.py",
        ROOT / "engine" / "edit_mode" / "connected_document_recovery.py",
        ROOT / "engine" / "app_actions" / "office_undo_services.py",
    ]
    paths.extend((ROOT / "engine" / "workflows").glob("*_services.py"))
    paths.extend((ROOT / "engine" / "edit_mode").glob("stage10_*_services.py"))
    paths.extend((ROOT / "engine" / "app_actions").glob("*_adapter.py"))
    return sorted(set(paths))


class MaintainabilityBudgetTests(unittest.TestCase):
    def test_high_risk_functions_stay_below_one_hundred_lines(self):
        violations = []
        for path in maintained_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                length = int(node.end_lineno) - int(node.lineno) + 1
                if length > MAX_FUNCTION_LINES:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}:"
                        f"{node.name}={length}"
                    )
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
