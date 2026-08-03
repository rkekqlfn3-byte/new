import unittest
from unittest.mock import patch

from engine.local_commands import LocalCommandAnalyzer
from engine.parser import CommandParser


class LocalCommandAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()
        self.parser.dict_mgr.noun_dict = {
            "메모장": "C:\\Windows\\notepad.exe",
            "계산기": "calc.exe",
        }
        self.parser.dict_mgr.noun_revision += 1

    def test_parser_owns_dedicated_local_analyzer(self):
        self.assertIsInstance(
            self.parser.local_command_analyzer, LocalCommandAnalyzer
        )
        self.assertIs(
            self.parser.dict_mgr, self.parser.local_command_analyzer.dict_mgr
        )
        self.assertIs(
            self.parser.template_matcher,
            self.parser.local_command_analyzer.template_matcher,
        )
        self.assertFalse(hasattr(self.parser.local_command_analyzer, "owner"))

    def test_public_analysis_delegates_to_local_analyzer(self):
        expected = {"kind": "single", "executable": False}
        with patch.object(
            self.parser.local_command_analyzer,
            "analyze_command",
            return_value=expected,
        ) as analyze:
            result = self.parser.analyze_command("테스트")

        self.assertIs(expected, result)
        analyze.assert_called_once_with("테스트")

    def test_app_index_refreshes_inside_analyzer(self):
        self.assertEqual(
            ("메모장", "C:\\Windows\\notepad.exe"),
            self.parser._identify_app("메모장 열어", ["메모장"], None, "OPEN"),
        )

        self.parser.dict_mgr.noun_dict["그림판"] = "mspaint.exe"
        self.parser.dict_mgr.noun_revision += 1

        self.assertEqual(
            ("그림판", "mspaint.exe"),
            self.parser._identify_app("그림판 열어", ["그림판"], None, "OPEN"),
        )

    def test_compound_analysis_remains_read_only(self):
        with patch("engine.parser.os.startfile") as startfile, patch(
            "engine.parser.subprocess.run"
        ) as run:
            analysis = self.parser.analyze_command("메모장 열고 계산기 켜")

        self.assertEqual("compound", analysis["kind"])
        self.assertEqual(
            [("OPEN", "메모장"), ("OPEN", "계산기")],
            [(step["macro"], step["app_name"]) for step in analysis["steps"]],
        )
        startfile.assert_not_called()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
