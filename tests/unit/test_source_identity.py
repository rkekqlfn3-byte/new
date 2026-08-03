import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from verification.source_identity import source_identity, source_identity_errors


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git is unavailable")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-source-id-")
        self.root = Path(self.temp_dir.name)
        (self.root / "engine").mkdir()
        (self.root / "verification").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "engine" / "sample.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.root / "verification" / "sample_report.json").write_text(
            json.dumps({"generated_at": "old"}), encoding="utf-8"
        )
        (self.root / "docs" / "notes.md").write_text("old\n", encoding="utf-8")
        self._git("init")
        self._git("config", "user.email", "jarvis-test@example.invalid")
        self._git("config", "user.name", "JARVIS Test")
        self._git("add", ".")
        self._git("commit", "-m", "fixture")

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def _git(self, *args):
        completed = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)

    def test_identity_changes_with_source_but_not_reports_or_docs(self):
        initial = source_identity(self.root)
        self.assertFalse(initial["dirty"])

        (self.root / "verification" / "sample_report.json").write_text(
            json.dumps({"generated_at": "new"}), encoding="utf-8"
        )
        (self.root / "docs" / "notes.md").write_text("new\n", encoding="utf-8")
        non_source_change = source_identity(self.root)
        self.assertEqual(initial, non_source_change)

        (self.root / "engine" / "sample.py").write_text(
            "VALUE = 2\n", encoding="utf-8"
        )
        source_change = source_identity(self.root)
        self.assertTrue(source_change["dirty"])
        self.assertNotEqual(initial["tree_hash"], source_change["tree_hash"])
        self.assertNotEqual(
            initial["changed_paths_hash"],
            source_change["changed_paths_hash"],
        )

    def test_mismatch_reasons_never_include_paths_or_contents(self):
        expected = source_identity(self.root)
        actual = dict(expected)
        actual["tree_hash"] = "0" * 64
        reasons = source_identity_errors(actual, expected)
        self.assertEqual(("probe_source_tree_hash_mismatch",), reasons)
        rendered = repr(reasons)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("sample.py", rendered)


if __name__ == "__main__":
    unittest.main()
