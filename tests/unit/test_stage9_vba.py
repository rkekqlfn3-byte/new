import unittest

from engine.app_actions.excel_vba_adapter import (
    ExcelVbaAdapter,
    analyze_vba_code,
    parse_vba_procedures,
)
from engine.edit_mode.stage9 import StructuredVbaIntentAnalyzer


class Stage9VbaAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StructuredVbaIntentAnalyzer()
        self.context = {"app_type": "excel"}

    def test_procedure_outline_marks_only_public_parameterless_sub_runnable(self):
        procedures = parse_vba_procedures(
            "Public Sub ExportReport()\nEnd Sub\n"
            "Private Sub Hidden()\nEnd Sub\n"
            "Function Total(value As Long) As Long\nEnd Function"
        )
        self.assertEqual(["ExportReport", "Hidden", "Total"], [p["name"] for p in procedures])
        self.assertEqual([True, False, False], [p["runnable"] for p in procedures])

    def test_static_analysis_reports_dangerous_capabilities_without_running_code(self):
        report = analyze_vba_code(
            'Sub Dangerous()\nShell("cmd.exe")\nKill "C:\\\\temp.txt"\n'
            'Set http = CreateObject("WinHttp.WinHttpRequest.5.1")\nEnd Sub'
        )
        self.assertEqual(
            ["file_delete", "network", "shell"],
            report["dangerous_capabilities"],
        )
        self.assertGreaterEqual(len(report["findings"]), 3)

    def test_roadmap_natural_requests_map_to_bounded_operations(self):
        cases = (
            ("VBA 모듈 목록 보여줘", "vba_inspect_project"),
            ("Module1 코드 보여줘", "vba_read_module"),
            ("이 매크로가 무슨 일을 해?", "vba_analyze_module"),
            ("마지막 행을 잘못 찾는데 고쳐줘", "vba_replace_module"),
            ("선택한 범위만 처리하도록 바꿔", "vba_replace_module"),
            ("실행 전에 원본 시트를 백업하도록 해", "vba_replace_module"),
            ("ExportReport 매크로 실행해줘", "vba_run_procedure"),
        )
        for command, operation in cases:
            with self.subTest(command=command):
                self.assertEqual(
                    operation,
                    self.analyzer.analyze(command, self.context).operation,
                )

    def test_exact_code_change_requires_two_quoted_fragments(self):
        intent = self.analyzer.analyze(
            'Module1 모듈에서 "Range(\"A1\")"를 "Selection"으로 바꿔',
            self.context,
        )
        self.assertEqual("exact_replace", intent.params["change_kind"])
        self.assertEqual("Module1", intent.params["module_name"])

        read = self.analyzer.analyze("Module1 코드 보여줘", self.context)
        self.assertEqual("Module1", read.params["module_name"])
        project = self.analyzer.analyze("VBA 모듈 목록 보여줘", self.context)
        self.assertEqual("", project.params["module_name"])

    def test_last_row_patch_qualifies_cells_and_rows_to_one_sheet(self):
        code = (
            "Sub FindLast()\n"
            "Dim ws As Worksheet\n"
            "Set ws = ThisWorkbook.Worksheets(1)\n"
            "lastRow = Cells(Rows.Count, 1).End(xlUp).Row\n"
            "End Sub"
        )
        patched = ExcelVbaAdapter._patch_last_row(code)
        self.assertIn(
            "ws.Cells(ws.Rows.Count, 1).End(xlUp).Row",
            patched,
        )

    def test_non_excel_requests_stay_outside_vba_route(self):
        self.assertIsNone(
            self.analyzer.analyze("이 매크로 설명해줘", {"app_type": "word"})
        )


if __name__ == "__main__":
    unittest.main()
