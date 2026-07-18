import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from engine.parser import CommandParser


class LLMActionValidationTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()
        self.parser.dict_mgr.noun_dict = {
            "메모장": "notepad.exe",
            "테스트앱": "C:\\Apps\\test.exe",
        }
        self.parser.dict_mgr.noun_revision += 1
        self.parser.dict_mgr.learned_macros = {
            "그림판": {
                "색상 변경": {
                    "code": "print('original')",
                    "description": "색상 변경",
                }
            }
        }

    def _run_with_result(self, result):
        self.parser.llm_engine.process_command = Mock(return_value=result)
        return self.parser.parse_and_execute("특수 요청 번호 98765")

    def test_registered_app_is_canonicalized_and_opened(self):
        result = {
            "response": "실행합니다.",
            "actions": [{"action": "open_app", "target": "메모장"}],
        }
        with patch("engine.parser.os.startfile") as startfile, patch.object(
            self.parser.action_executor,
            "focus_window_when_ready",
            return_value=101,
        ) as focus:
            response = self._run_with_result(result)

        startfile.assert_called_once_with("notepad.exe")
        focus.assert_called_once_with("메모장")
        self.assertEqual("실행합니다.", response)

    def test_unknown_app_is_not_executed(self):
        result = {
            "response": "실행합니다.",
            "actions": [{"action": "open_app", "target": "존재하지않는앱"}],
        }
        with patch("engine.parser.os.startfile") as startfile:
            response = self._run_with_result(result)

        startfile.assert_not_called()
        self.assertIn("등록된 앱 또는 웹사이트", response)

    def test_missing_file_is_rejected_before_analysis(self):
        missing = os.path.join(tempfile.gettempdir(), "jarvis-file-that-does-not-exist.txt")
        result = {
            "response": "파일을 확인합니다.",
            "actions": [{"action": "read_and_analyze", "target": missing}],
        }
        response = self._run_with_result(result)

        self.assertIn("실제로 존재하는 파일 경로", response)
        self.parser.llm_engine.process_command.assert_called_once()

    def test_file_content_uses_question_mode_for_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "report.txt")
            with open(path, "w", encoding="utf-8") as file:
                file.write("매출은 전월 대비 20퍼센트 증가했습니다.")

            self.parser.llm_engine.process_command = Mock(side_effect=[
                {
                    "response": "파일을 분석합니다.",
                    "actions": [{"action": "read_and_analyze", "target": path}],
                },
                {"response": "매출이 20퍼센트 증가했습니다.", "action": "none"},
            ])
            response = self.parser.parse_and_execute("특수 요청 번호 98765")

        self.assertIn("매출이 20퍼센트 증가했습니다.", response)
        second_call = self.parser.llm_engine.process_command.call_args_list[1]
        self.assertEqual("question", second_call.kwargs["mode"])

    def test_invalid_generated_python_is_not_executed(self):
        result = {
            "response": "자동화를 실행합니다.",
            "actions": [{
                "action": "dynamic_code",
                "code": "if True print('broken')",
                "explanation_steps": [{"step": "실행", "code_snippet": "broken"}],
            }],
        }
        with patch("engine.parser.subprocess.run") as run:
            response = self._run_with_result(result)

        run.assert_not_called()
        self.assertIn("파이썬 문법 오류", response)

    def test_missing_learned_macro_is_not_executed(self):
        result = {
            "response": "매크로를 실행합니다.",
            "actions": [{
                "action": "use_learned_macro",
                "app_name": "그림판",
                "macro_name": "없는 매크로",
            }],
        }
        with patch("engine.parser.subprocess.run") as run:
            response = self._run_with_result(result)

        run.assert_not_called()
        self.assertIn("등록된 학습 매크로", response)

    def test_dynamic_code_without_external_target_is_rejected(self):
        result = {
            "response": "자동화를 실행합니다.",
            "actions": [{
                "action": "dynamic_code",
                "code": "print('hardcoded target')",
                "explanation_steps": [{
                    "step": "고정값 출력",
                    "code_snippet": "print('hardcoded target')",
                }],
            }],
        }
        with patch("engine.parser.subprocess.run") as run:
            response = self._run_with_result(result)

        run.assert_not_called()
        self.assertIn("sys.argv", response)

    def test_json_slot_macro_must_parse_json_argument(self):
        result = {
            "response": "자동화를 실행합니다.",
            "actions": [{
                "action": "dynamic_code",
                "code": "import sys\nprint(sys.argv[1])",
                "explanation_steps": [{
                    "step": "인자 출력",
                    "code_snippet": "print(sys.argv[1])",
                }],
                "learning": {
                    "intent": "MOVE_WINDOW",
                    "argument_mode": "json",
                    "verbs": [], "nouns": [], "utterances": [],
                    "slots": [{
                        "name": "app", "type": "app", "value": "메모장",
                        "required": True,
                    }],
                },
            }],
        }
        with patch("engine.parser.subprocess.run") as run:
            response = self._run_with_result(result)
        run.assert_not_called()
        self.assertIn("json.loads", response)

    def test_parameterless_dynamic_code_does_not_require_dummy_argv_usage(self):
        result = {
            "response": "현재 Excel 창 제목을 표시합니다.",
            "actions": [{
                "action": "dynamic_code",
                "target": "현재 실행 중인 Excel",
                "app_name": "Excel",
                "macro_name": "현재 Excel 창 제목 표시",
                "description": "활성 Excel 창 제목을 메시지 박스로 표시",
                "code": (
                    "import win32api\n"
                    "import win32com.client\n"
                    "import pythoncom\n"
                    "pythoncom.CoInitialize()\n"
                    "try:\n"
                    "    excel = win32com.client.GetActiveObject('Excel.Application')\n"
                    "    win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)\n"
                    "finally:\n"
                    "    excel = None\n"
                    "    pythoncom.CoUninitialize()"
                ),
                "explanation_steps": [{
                    "step": "Excel 창 제목 표시",
                    "code_snippet": "win32api.MessageBox(0, excel.Caption, 'JARVIS', 0)",
                }],
                "learning": {
                    "intent": "SHOW_ACTIVE_EXCEL_TITLE",
                    "argument_mode": "json",
                    "verbs": ["보여줘"],
                    "nouns": [],
                    "utterances": ["현재 Excel 창 제목을 보여줘"],
                    "slots": [],
                },
            }],
        }

        _, actions, issues = self.parser._validate_llm_result(result)

        self.assertEqual([], issues)
        self.assertEqual(1, len(actions))
        self.assertNotIn("sys.argv", actions[0]["code"])

    def test_office_code_requires_balanced_com_apartment(self):
        action = {
            "target": "실행 중인 Excel",
            "app_name": "Excel",
            "code": (
                "import win32com.client\n"
                "excel = win32com.client.GetActiveObject('Excel.Application')\n"
                "print(excel.Caption)"
            ),
            "explanation_steps": [{
                "step": "Excel 조회",
                "code_snippet": "print(excel.Caption)",
            }],
            "learning": {"argument_mode": "json", "slots": []},
        }

        issue = self.parser._validate_generated_code(
            action, require_external_target=True
        )

        self.assertIn("CoInitialize", issue)
        self.assertIn("CoUninitialize", issue)

    def test_office_code_cannot_quit_attached_application(self):
        action = {
            "target": "실행 중인 Excel",
            "app_name": "Excel",
            "code": (
                "import pythoncom\n"
                "import win32com.client\n"
                "pythoncom.CoInitialize()\n"
                "try:\n"
                "    excel = win32com.client.GetActiveObject('Excel.Application')\n"
                "    excel.Quit()\n"
                "finally:\n"
                "    excel = None\n"
                "    pythoncom.CoUninitialize()"
            ),
            "explanation_steps": [{
                "step": "Excel 종료",
                "code_snippet": "excel.Quit()",
            }],
            "learning": {"argument_mode": "json", "slots": []},
        }

        issue = self.parser._validate_generated_code(
            action, require_external_target=True
        )

        self.assertIn("Quit()", issue)

    def test_excel_title_rejects_jarvis_foreground_window_lookup(self):
        action = {
            "target": "현재 Excel 창 제목",
            "app_name": "Excel",
            "macro_name": "Excel 창 제목 표시",
            "description": "활성 Excel 제목을 표시",
            "code": (
                "import win32gui\n"
                "hwnd = win32gui.GetForegroundWindow()\n"
                "title = win32gui.GetWindowText(hwnd)\n"
                "print(title)"
            ),
            "explanation_steps": [{
                "step": "전면 창 제목 조회",
                "code_snippet": "win32gui.GetForegroundWindow()",
            }],
            "learning": {"argument_mode": "json", "slots": []},
        }

        issue = self.parser._validate_generated_code(
            action, require_external_target=True
        )

        self.assertIn("ActiveWindow.Caption", issue)

    def test_action_plan_with_empty_learning_metadata_is_not_executed(self):
        result = {
            "response": "창을 이동합니다.",
            "actions": [{
                "action": "action_plan",
                "macro_name": "",
                "plan": [{
                    "action": "move_window", "target": "메모장", "direction": "오른쪽",
                    "keys": [], "text": "", "seconds": 0,
                    "x": 0, "y": 0, "width": 0, "height": 0,
                }],
                "learning": {
                    "intent": "", "argument_mode": "json", "verbs": [],
                    "nouns": [], "utterances": [], "slots": [],
                },
            }],
        }
        with patch.object(self.parser.action_executor, "execute_plan") as execute:
            response = self._run_with_result(result)
        execute.assert_not_called()
        self.assertIn("학습 정보가 비어", response)

    def test_action_plan_description_fills_omitted_triggers_and_verbs(self):
        result = {
            "response": "날짜를 표시합니다.",
            "actions": [{
                "action": "action_plan",
                "macro_name": "날짜_표시",
                "description": "오늘 날짜를 메시지 박스로 보여줍니다.",
                "plan": [{"action": "wait", "seconds": 0.1}],
                "learning": {"intent": "SHOW_DATE", "verbs": [], "utterances": []},
            }],
        }

        _, actions, issues = self.parser._validate_llm_result(result)

        self.assertEqual([], issues)
        self.assertEqual(
            ["오늘 날짜를 메시지 박스로 보여줘"],
            actions[0]["learning"]["utterances"],
        )
        self.assertEqual(["보여줘"], actions[0]["learning"]["verbs"])

    def test_non_json_result_becomes_explanation_only(self):
        with patch("engine.parser.os.startfile") as startfile, \
             patch("engine.parser.subprocess.run") as run:
            response = self._run_with_result("일반 텍스트 응답")

        startfile.assert_not_called()
        run.assert_not_called()
        self.assertIn("일반 텍스트 응답", response)
        self.assertIn("올바른 JSON 객체", response)


if __name__ == "__main__":
    unittest.main()
