"""Regression tests for read-only maintenance automation."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from verification.maintenance_audit import (
    audit_forbidden_back_references,
    audit_large_workspace_files,
    audit_module_line_budgets,
    audit_parser_budget,
    audit_secrets,
    audit_tracked_artifacts,
    repository_paths,
    retained_parent_reference_paths,
)
from verification.test_gate_selector import select_test_gates


class TestGateSelectorTests(unittest.TestCase):
    def test_document_only_change_keeps_the_lightweight_gates(self):
        gates = select_test_gates(["docs/guide.md"])
        self.assertEqual(
            ["maintenance_audit", "diff_check"],
            [gate.gate_id for gate in gates],
        )

    def test_parser_change_selects_full_and_utterance_gates(self):
        gate_ids = {
            gate.gate_id for gate in select_test_gates(["engine/parser.py"])
        }
        self.assertTrue({
            "compile", "full_tests", "startup", "parser_contract",
            "utterance_battery", "performance",
        }.issubset(gate_ids))

    def test_edit_change_includes_windows_and_manual_validation(self):
        gates = select_test_gates(["engine/edit_mode/controller.py"])
        by_id = {gate.gate_id: gate for gate in gates}
        self.assertIn("edit_windows", by_id)
        self.assertTrue(by_id["edit_manual"].manual)

    def test_storage_and_confirmation_changes_select_failure_injection(self):
        for path in (
            "engine/storage/json_store.py",
            "engine/confirmation/confirmation_registry.py",
        ):
            with self.subTest(path=path):
                gate_ids = {
                    gate.gate_id for gate in select_test_gates([path])
                }
                self.assertIn("failure_injection", gate_ids)

    def test_repeated_paths_do_not_duplicate_gates(self):
        gates = select_test_gates([
            "engine/security/a.py", "engine/security/b.py", "engine/security/a.py",
        ])
        gate_ids = [gate.gate_id for gate in gates]
        self.assertEqual(len(gate_ids), len(set(gate_ids)))


class MaintenanceAuditTests(unittest.TestCase):
    def test_identity_scan_finds_renamed_container_and_bound_method_references(self):
        class Parent:
            def execute(self):
                return None

        class Child:
            pass

        parent = Parent()
        child = Child()
        child._host = parent
        child.services = {"runtime": parent}
        child.callback = parent.execute

        paths = retained_parent_reference_paths(child, parent)

        self.assertTrue(any(path.endswith("._host") for path in paths))
        self.assertTrue(any(".mapping[" in path for path in paths))
        self.assertTrue(any(path.endswith(".__self__") for path in paths))

    def test_parser_line_budget_reports_only_the_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            parser_path = root / "engine" / "parser.py"
            parser_path.parent.mkdir()
            parser_path.write_text("line\n" * 4, encoding="utf-8")

            findings = audit_parser_budget(root, max_lines=3)

        self.assertEqual("parser_line_budget_exceeded", findings[0].code)
        self.assertIn("line_count=4", findings[0].detail)

    def test_large_module_budget_blocks_only_growth_past_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "engine" / "large.py"
            path.parent.mkdir()
            path.write_text("line\n" * 4, encoding="utf-8")

            within = audit_module_line_budgets(
                root, {"engine/large.py": 4}
            )
            exceeded = audit_module_line_budgets(
                root, {"engine/large.py": 3}
            )

        self.assertEqual([], within)
        self.assertEqual("module_line_budget_exceeded", exceeded[0].code)
        self.assertIn("line_count=4", exceeded[0].detail)

    def test_forbidden_back_reference_is_found_by_ast(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "engine" / "pipeline" / "bad.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                "class Bad:\n    def __init__(self, value):\n        self._parser = value\n",
                encoding="utf-8",
            )

            findings = audit_forbidden_back_references(root)

        self.assertEqual("forbidden_parent_back_reference", findings[0].code)
        self.assertIn("self._parser", findings[0].detail)

    def test_secret_finding_never_contains_the_secret_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret = "sk-" + "1234567890abcdefghijklmn"
            path = root / "settings.py"
            assignment = "api" + "_key = " + repr(secret) + "\n"
            path.write_text(assignment, encoding="utf-8")

            findings = audit_secrets(root, ["settings.py"])

        self.assertTrue(findings)
        self.assertNotIn(secret, repr(findings))

    def test_tracked_generated_artifacts_are_errors(self):
        findings = audit_tracked_artifacts(
            Path("."), ["dist/Jarvis.exe", "engine/parser.py"]
        )
        codes = {finding.code for finding in findings}
        self.assertIn("tracked_private_or_generated_directory", codes)
        self.assertIn("tracked_generated_artifact", codes)

    def test_git_path_enumeration_preserves_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(
                ["git", "init", "-q"], cwd=root, check=True,
                capture_output=True,
            )
            relative = "outputs/한글 검사 문서.txt"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text("owned fixture", encoding="utf-8")
            subprocess.run(
                ["git", "add", "--", relative], cwd=root, check=True,
                capture_output=True,
            )

            tracked, untracked = repository_paths(root)
            findings = audit_tracked_artifacts(root, tracked)

        self.assertEqual((relative,), tracked)
        self.assertEqual((), untracked)
        self.assertIn(
            "tracked_private_or_generated_directory",
            {finding.code for finding in findings},
        )

    def test_generated_directory_is_one_warning_and_not_walked_per_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "outputs" / "node_modules"
            output.mkdir(parents=True)
            (output / "large.bin").write_bytes(b"x" * 32)
            source = root / "source.bin"
            source.write_bytes(b"x" * 32)

            findings = audit_large_workspace_files(root, threshold=16)

        generated = [
            item for item in findings
            if item.code == "generated_workspace_directory_present"
        ]
        large = [item for item in findings if item.code == "large_workspace_file"]
        self.assertEqual(1, len(generated))
        self.assertEqual(["source.bin"], [item.path for item in large])


if __name__ == "__main__":
    unittest.main()
