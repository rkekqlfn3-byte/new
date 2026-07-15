import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.runtime_paths import _default_user_data_dir
from tests.test_runner import TEST_MODULES
from verification.release_security_audit import audit_default_data, scan_blob
from verification.runtime_entrypoint import configure_pywin32_dlls


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ReleaseDefaultDataTests(unittest.TestCase):
    def test_repository_default_data_is_clean(self):
        self.assertEqual([], audit_default_data(PROJECT_ROOT / "default_data"))

    def test_nonempty_default_api_key_and_memory_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "default_data"
            shutil.copytree(PROJECT_ROOT / "default_data", target)
            dictionaries = json.loads(
                (target / "dictionaries.json").read_text(encoding="utf-8")
            )
            dictionaries["ai_config"]["api_key"] = "unit-test-secret-value"
            (target / "dictionaries.json").write_text(
                json.dumps(dictionaries), encoding="utf-8"
            )
            (target / "user_memory.json").write_text(
                json.dumps({"user_info": "private test memory"}), encoding="utf-8"
            )
            codes = {finding.code for finding in audit_default_data(target)}
            self.assertIn("default_contains_api_key", codes)
            self.assertIn("default_contains_memory", codes)

    def test_binary_scanner_redacts_and_detects_known_secret(self):
        secret = b"unit-test-secret-value"
        findings = scan_blob(b"prefix-" + secret + b"-suffix", "test.bin", [secret])
        self.assertEqual("known_secret", findings[0].code)
        self.assertNotIn(secret.decode(), findings[0].detail)

    def test_binary_scanner_detects_common_token_shape(self):
        findings = scan_blob(b"sk-" + b"A" * 24, "test.bin")
        self.assertIn("openai_key_pattern", {item.code for item in findings})

    def test_build_script_does_not_blindly_trust_a_copied_virtualenv(self):
        script = (PROJECT_ROOT / "build_dist.bat").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn("if defined jarvis_python goto python_selected", script)
        self.assertIn('".venv\\scripts\\python.exe" -c "import sys"', script)
        self.assertIn(
            'if not errorlevel 1 set "jarvis_python=.venv\\scripts\\python.exe"',
            script,
        )
        self.assertIn(
            'if not defined jarvis_python set "jarvis_python=python"', script
        )

    def test_start_script_validates_a_copied_virtualenv_before_use(self):
        # Jarvis_Start.bat must not launch a stale copied venv blindly; it should
        # verify the interpreter starts and fall back to the system pythonw.
        script = (PROJECT_ROOT / "Jarvis_Start.bat").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn('".venv\\scripts\\python.exe" -c "import sys"', script)
        self.assertIn("where pythonw", script)
        self.assertIn('set "jarvis_pythonw=pythonw"', script)

    def test_build_regression_gate_lists_every_test_module(self):
        discovered = {
            ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts)
            for path in (PROJECT_ROOT / "tests").glob("*/test_*.py")
        }
        self.assertEqual(discovered, set(TEST_MODULES))

    def test_build_uses_the_portable_runtime_entrypoint(self):
        script = (PROJECT_ROOT / "build_dist.bat").read_text(
            encoding="utf-8"
        ).lower()
        self.assertEqual(5, script.count("verification.runtime_entrypoint"))
        self.assertIsInstance(configure_pywin32_dlls(), list)


class SeparatedRuntimePathTests(unittest.TestCase):
    def test_explicit_data_override_is_used_for_source_and_exe(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"JARVIS_DATA_DIR": temp_dir}
        ):
            self.assertEqual(Path(temp_dir).resolve(), _default_user_data_dir())

    def test_source_default_uses_local_app_data_not_project_data(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"LOCALAPPDATA": temp_dir}, clear=False
        ):
            previous = os.environ.pop("JARVIS_DATA_DIR", None)
            try:
                self.assertEqual(
                    Path(temp_dir) / "Jarvis" / "data",
                    _default_user_data_dir(),
                )
            finally:
                if previous is not None:
                    os.environ["JARVIS_DATA_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
