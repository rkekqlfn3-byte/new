"""Security regression tests for every dynamic Python execution path."""

import os
import tempfile
import unittest
from unittest import mock

from engine.builtins import BuiltinMacros
from engine.execution_result import success_result
from engine.llm_engine import LLMEngine
from engine.managers.dict_manager import DictionaryManager
from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)
from engine.parser import CommandParser
from engine.security import BLOCKED, CONFIRMATION_REQUIRED, SAFE, DynamicCodePreflight


class DynamicCodePolicyTests(unittest.TestCase):
    def setUp(self):
        self.preflight = DynamicCodePreflight()

    def test_data_processing_code_is_safe(self):
        result = self.preflight.analyze(
            "import json, sys\nargs=json.loads(sys.argv[1])\nprint(args.get('value', 0) + 1)"
        )
        self.assertEqual(SAFE, result.status)
        self.assertEqual([], list(result.findings))

    def test_file_process_network_and_app_changes_require_confirmation(self):
        cases = [
            "open('C:/Temp/result.txt', 'w', encoding='utf-8').write('ok')",
            "import subprocess\nsubprocess.run(['notepad.exe'])",
            "import requests\nrequests.post('https://example.com', data='value')",
            "import win32com.client\nwin32com.client.GetActiveObject('Excel.Application')",
            "import ctypes\nctypes.windll.user32.keybd_event(175, 0, 0, 0)",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.assertEqual(
                    CONFIRMATION_REQUIRED,
                    self.preflight.analyze(code).status,
                )

    def test_supported_excel_title_message_box_requires_confirmation(self):
        result = self.preflight.analyze(
            "import win32api\n"
            "import win32com.client\n"
            "excel = win32com.client.GetActiveObject('Excel.Application')\n"
            "win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)"
        )
        self.assertEqual(CONFIRMATION_REQUIRED, result.status)
        self.assertNotIn(
            "unknown_import",
            {finding.category for finding in result.findings},
        )

    def test_message_box_alone_requires_confirmation(self):
        result = self.preflight.analyze(
            "import win32api\n"
            "win32api.MessageBox(0, 'today', 'date', 0x00050040)"
        )
        self.assertEqual(CONFIRMATION_REQUIRED, result.status)
        self.assertIn(
            "windows_message_box", {finding.code for finding in result.findings}
        )

    def test_unavailable_packaged_modules_are_blocked_with_replacements(self):
        cases = [
            ("import pygetwindow", "win32gui"),
            ("import tkinter", "win32api.MessageBox"),
            ("import pyautogui", "pywinauto"),
            ("import numpy", "numpy"),
        ]
        for code, guidance in cases:
            with self.subTest(code=code):
                result = self.preflight.analyze(code)
                self.assertEqual(BLOCKED, result.status)
                self.assertTrue(
                    any(item.code.startswith("unsupported_module:") for item in result.findings)
                )
                self.assertIn(guidance, "\n".join(item.message for item in result.findings))

    def test_shell_registry_credentials_and_recursive_delete_are_blocked(self):
        cases = [
            "import subprocess as sp\nsp.run(['powershell.exe', '-c', 'dir'])",
            "import subprocess\nsubprocess.getoutput('dir')",
            "from os import system as run\nrun('cmd /c dir')",
            "import os\nos.spawnl(os.P_NOWAIT, 'notepad.exe', 'notepad.exe')",
            "import winreg\nwinreg.SetValueEx(None, 'x', 0, 1, 'y')",
            "import os\nprint(os.getenv('OPENAI_API_KEY'))",
            "import os\nprint(os.environ.get('OPENAI_API_KEY'))",
            "import os\nremove = os.system\nremove('dir')",
            "from os import *\nsystem('dir')",
            "import shutil\nshutil.rmtree('C:/Temp/all')",
            "getattr(__builtins__, 'eval')('1+1')",
            "import operator\noperator.attrgetter('__subclasses__')(object)()",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.assertEqual(BLOCKED, self.preflight.analyze(code).status)

    def test_broad_delete_and_jarvis_self_modification_are_blocked(self):
        broad = self.preflight.analyze(
            "import glob, os\nfor path in glob.glob('C:/Temp/*'):\n    os.remove(path)"
        )
        self.assertEqual(BLOCKED, broad.status)
        self.assertIn("broad_file_delete", {item.code for item in broad.findings})

        protected = self.preflight.analyze(
            "import sys\nopen(sys.argv[1], 'w').write('changed')",
            argument=str(self.preflight.protected_root / "jarvis_app.py"),
        )
        self.assertEqual(BLOCKED, protected.status)
        self.assertIn(
            "protected_root_mutation", {item.code for item in protected.findings}
        )

        sensitive = self.preflight.analyze(
            "from pathlib import Path\n"
            "print((Path.home() / '.ssh' / 'id_rsa').read_text())"
        )
        self.assertEqual(BLOCKED, sensitive.status)
        self.assertIn("sensitive_file_access", {item.code for item in sensitive.findings})

    def test_approval_fingerprint_binds_code_argument_and_context(self):
        first = self.preflight.analyze("open('x', 'w').write('1')")
        same = self.preflight.analyze("open('x', 'w').write('1')")
        changed = self.preflight.analyze("open('x', 'w').write('2')")
        context = {"action": "dynamic_code", "target": "x"}
        self.assertEqual(
            first.approval_fingerprint("x", context),
            same.approval_fingerprint("x", context),
        )
        self.assertNotEqual(
            first.approval_fingerprint("x", context),
            changed.approval_fingerprint("x", context),
        )
        self.assertNotEqual(
            first.approval_fingerprint("x", context),
            first.approval_fingerprint("y", context),
        )

    def test_json_file_argument_is_not_mistaken_for_jarvis_source_path(self):
        result = self.preflight.analyze(
            "import json, sys\n"
            "args = json.loads(sys.argv[1])\n"
            "open(args['path'], 'w').write('ok')",
            argument='{"path":"C:/Temp/jarvis-result.txt"}',
        )
        self.assertEqual(CONFIRMATION_REQUIRED, result.status)
        self.assertNotIn(
            "protected_root_mutation", {item.code for item in result.findings}
        )


class DynamicCodeParserIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jarvis-preflight-test-")
        dictionary_path = os.path.join(self.temp_dir.name, "dictionaries.json")
        candidate_path = os.path.join(self.temp_dir.name, "candidates.json")
        self.parser = CommandParser(
            native_action_candidate_manager=NativeActionCandidateManager(candidate_path)
        )
        self.parser.dict_mgr = DictionaryManager(dictionary_path)
        self.parser.llm_engine = LLMEngine(self.parser.dict_mgr)
        self.parser.builtins = BuiltinMacros(self.parser.dict_mgr, self.parser)
        self.parser.action_executor.noun_dict = self.parser.dict_mgr.noun_dict
        self.runner_result = success_result(
            "ran",
            action="python_macro",
            verified=False,
            verification_status="confirmation_required",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _dynamic_action(self, code, target=None):
        return {
            "action": "dynamic_code",
            "app_name": "시스템",
            "macro_name": "secure_test",
            "description": "preflight 테스트",
            "target": target or os.path.join(self.temp_dir.name, "result.txt"),
            "code": code,
            "explanation_steps": [
                {"step": "테스트 실행", "code_snippet": "sys.argv[1]"}
            ],
            "learning": {
                "intent": "SECURE_TEST",
                "argument_mode": "json",
                "verbs": ["테스트"],
                "nouns": [],
                "utterances": ["보안 테스트"],
                "slots": [],
            },
        }

    def _run_ai_result(self, result, session_id="security-session"):
        self.parser.llm_engine.process_command = mock.Mock(return_value=result)
        return self.parser.execute_command_result(
            "특수 보안 테스트 요청", use_api=True, session_id=session_id
        )

    def test_risky_batch_waits_before_any_action_and_runs_once_after_approval(self):
        self.parser.dict_mgr.noun_dict = {"메모장": "notepad.exe"}
        self.parser.dict_mgr.noun_revision += 1
        result = {
            "response": "작업을 실행합니다.",
            "actions": [
                {"action": "open_app", "target": "메모장"},
                self._dynamic_action(
                    "import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write('ok')"
                ),
            ],
        }
        with mock.patch("engine.parser.os.startfile") as startfile, mock.patch.object(
            self.parser.action_executor,
            "focus_window_when_ready",
            return_value=101,
        ) as focus, mock.patch.object(
            self.parser.macro_runner, "run", return_value=self.runner_result
        ) as run:
            waiting = self._run_ai_result(result)
            self.assertEqual("confirmation_required", waiting["status"])
            startfile.assert_not_called()
            run.assert_not_called()
            self.assertEqual([], self.parser.pending_macros)

            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            completed = self.parser.resolve_pending_confirmation(
                "security-session", confirmation_id, "run_once"
            )
            self.assertTrue(completed["success"])
            startfile.assert_called_once_with("notepad.exe")
            focus.assert_called_once_with("메모장")
            run.assert_called_once()
            self.assertEqual(1, len(self.parser.pending_macros))

            duplicate = self.parser.resolve_pending_confirmation(
                "security-session", confirmation_id, "run_once"
            )
            self.assertFalse(duplicate["success"])
            self.assertEqual(1, run.call_count)

    def test_cancelling_confirmation_keeps_external_boundaries_untouched(self):
        result = {
            "response": "파일을 씁니다.",
            "actions": [self._dynamic_action(
                "import sys\nopen(sys.argv[1], 'w').write('cancelled')"
            )],
        }
        with mock.patch.object(self.parser.macro_runner, "run") as run:
            waiting = self._run_ai_result(result)
            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            cancelled = self.parser.resolve_pending_confirmation(
                "security-session", confirmation_id, "cancel"
            )
        self.assertFalse(cancelled["success"])
        self.assertEqual("user_cancelled", cancelled["error_type"])
        run.assert_not_called()
        self.assertEqual([], self.parser.pending_macros)

    def test_blocked_dynamic_code_is_never_executed_or_learned(self):
        result = {
            "response": "레지스트리를 변경합니다.",
            "actions": [self._dynamic_action(
                "import sys, winreg\nprint(sys.argv[1])\n"
                "winreg.SetValueEx(None, 'x', 0, 1, 'y')"
            )],
        }
        with mock.patch.object(self.parser.macro_runner, "run") as run:
            blocked = self._run_ai_result(result)
        self.assertEqual("blocked", blocked["status"])
        run.assert_not_called()
        self.assertEqual([], self.parser.pending_macros)
        self.assertIsNone(
            self.parser.get_pending_confirmation("security-session")
        )

    def test_adapted_macro_uses_the_same_confirmation_gate(self):
        self.parser.dict_mgr.learned_macros = {
            "시스템": {
                "기존 매크로": {
                    "code": "print('original')",
                    "description": "기존 매크로",
                    "state": "active",
                }
            }
        }
        target = os.path.join(self.temp_dir.name, "adapted.txt")
        result = {
            "response": "응용합니다.",
            "actions": [{
                "action": "adapted_macro",
                "app_name": "시스템",
                "macro_name": "기존 매크로",
                "target": target,
                "code": "import sys\nopen(sys.argv[1], 'w').write('adapted')",
                "explanation_steps": [
                    {"step": "응용", "code_snippet": "open(sys.argv[1], 'w')"}
                ],
            }],
        }
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=self.runner_result
        ) as run:
            waiting = self._run_ai_result(result)
            self.assertEqual("confirmation_required", waiting["status"])
            run.assert_not_called()
            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            completed = self.parser.resolve_pending_confirmation(
                "security-session", confirmation_id, "run_once"
            )
        self.assertTrue(completed["success"])
        run.assert_called_once_with(result["actions"][0]["code"], target)

    def test_direct_learned_macro_is_rechecked_and_code_change_is_rejected(self):
        target = os.path.join(self.temp_dir.name, "learned.txt")
        code = "import sys\nopen(sys.argv[1], 'w').write('first')"
        learned = {
            "code": code,
            "description": "저장된 파일 쓰기",
            "default_target": target,
            "state": "active",
            "verification_status": "confirmation_required",
            "learning": {"argument_mode": "text", "intent": "FILE_WRITE"},
        }
        self.parser.dict_mgr.learned_macros = {"시스템": {"파일쓰기": learned}}
        self.parser.dict_mgr.macro_dict = {
            "파일쓰기": {
                "type": "learned",
                "app": "시스템",
                "synonyms": ["파일쓰기"],
            }
        }

        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=self.runner_result
        ) as run:
            waiting = self.parser.execute_command_result(
                "파일쓰기", session_id="learned-session"
            )
            self.assertEqual("confirmation_required", waiting["status"])
            run.assert_not_called()
            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]

            learned["code"] = "import sys\nopen(sys.argv[1], 'w').write('changed')"
            changed = self.parser.resolve_pending_confirmation(
                "learned-session", confirmation_id, "run_once"
            )

        self.assertFalse(changed["success"])
        self.assertEqual("context_changed", changed["status"])
        run.assert_not_called()

    def test_direct_learned_message_box_waits_for_confirmation(self):
        code = (
            "import win32api\n"
            "win32api.MessageBox(0, 'today', 'date', 0x00050040)"
        )
        learned = {
            "code": code,
            "description": "테스트 알림 메시지 박스",
            "default_target": "",
            "state": "active",
            "verification_status": "user_confirmed",
            "learning": {"argument_mode": "none", "intent": "SHOW_TEST_ALERT"},
        }
        self.parser.dict_mgr.learned_macros = {"시스템": {"날짜보기": learned}}
        self.parser.dict_mgr.macro_dict = {
            "날짜보기": {
                "type": "learned",
                "app": "시스템",
                "synonyms": ["테스트 알림창 보여줘"],
            }
        }

        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=self.runner_result
        ) as run:
            waiting = self.parser.execute_command_result(
                "테스트 알림창 보여줘", session_id="message-box-session"
            )
            self.assertEqual("confirmation_required", waiting["status"])
            self.assertIn(
                "Windows 메시지 박스",
                waiting["data"]["confirmation"]["message"],
            )
            run.assert_not_called()

            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            completed = self.parser.resolve_pending_confirmation(
                "message-box-session", confirmation_id, "run_once"
            )

        self.assertTrue(completed["success"])
        run.assert_called_once_with(code, "")

    def test_ai_selected_stored_macro_also_uses_the_common_gate(self):
        target = os.path.join(self.temp_dir.name, "ai-learned.txt")
        code = "import sys\nopen(sys.argv[1], 'w').write('stored')"
        self.parser.dict_mgr.learned_macros = {
            "시스템": {
                "저장 매크로": {
                    "code": code,
                    "description": "저장된 매크로",
                    "state": "active",
                    "verification_status": "confirmation_required",
                }
            }
        }
        result = {
            "response": "저장된 작업을 실행합니다.",
            "actions": [{
                "action": "use_learned_macro",
                "app_name": "시스템",
                "macro_name": "저장 매크로",
                "target": target,
            }],
        }
        with mock.patch.object(
            self.parser.macro_runner, "run", return_value=self.runner_result
        ) as run:
            waiting = self._run_ai_result(result)
            self.assertEqual("confirmation_required", waiting["status"])
            run.assert_not_called()
            confirmation_id = waiting["data"]["confirmation"]["confirmation_id"]
            completed = self.parser.resolve_pending_confirmation(
                "security-session", confirmation_id, "run_once"
            )
        self.assertTrue(completed["success"])
        run.assert_called_once_with(code, target)


if __name__ == "__main__":
    unittest.main()
