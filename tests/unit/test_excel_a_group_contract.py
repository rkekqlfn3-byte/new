import unittest
from pathlib import Path

from engine.app_actions.excel_adapter import ExcelAdapter
from engine.edit_mode.stage5 import Stage5NativeEditAdapter
from engine.parsing.office_command_parser import (
    parse_native_excel_filter_command,
    parse_native_excel_find_replace_command,
    parse_native_excel_range_format_command,
    parse_native_excel_sort_command,
    parse_native_excel_sum_command,
    parse_native_excel_write_command,
)
from engine.user_feedback.templates import ACTION_LABELS


class ExcelAGroupContractTests(unittest.TestCase):
    def test_live_probes_are_isolated_content_free_and_fail_closed(self):
        root = Path(__file__).resolve().parents[2]
        core = (root / "verification" / "phase4_excel_core_probe.py").read_text(
            encoding="utf-8"
        )
        aux = (root / "verification" / "phase6_excel_aux_probe.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("GetActiveObject", core)
        self.assertIn('DispatchEx("Excel.Application")', core)
        self.assertIn('"user_excel_instance_attached"] = False', core)
        self.assertIn('return 0 if report.get("success") else 1', core)
        self.assertIn('"paths_or_contents_reported": False', aux)
        self.assertNotIn('report["sort_before"]', aux)
        self.assertIn('return 0 if report.get("success") else 1', aux)

    def test_native_operations_and_reversible_subset_are_explicit(self):
        required = {
            "write_cell", "sum_column_to_cell", "format_range",
            "filter_range", "find_replace", "sort_range",
            "insert_rows", "insert_columns",
        }
        self.assertTrue(required.issubset(ExcelAdapter.supported_operations))
        self.assertEqual(required, set(ExcelAdapter.undo_supported_operations))
        self.assertTrue(
            {"read_selection", "insert_rows", "insert_columns"}.issubset(
                Stage5NativeEditAdapter.supported_operations
            )
        )

    def test_value_formula_sum_and_average_have_structured_local_routes(self):
        value = parse_native_excel_write_command("엑셀 A1에 10 입력해줘")
        formula = parse_native_excel_write_command("엑셀 B1에 =A1*2 입력해줘")
        total = parse_native_excel_sum_command(
            "엑셀 A2:A10 합계를 A11에 넣어줘"
        )
        average = parse_native_excel_sum_command(
            "엑셀 A2:A10 평균을 A11에 넣어줘"
        )
        self.assertEqual("auto", value["params"]["value_type"])
        self.assertEqual("formula", formula["params"]["value_type"])
        self.assertEqual("sum", total["params"]["aggregation"])
        self.assertEqual("average", average["params"]["aggregation"])

    def test_format_filter_sort_and_replace_have_structured_routes(self):
        bold_off = parse_native_excel_range_format_command(
            "엑셀 A1:B3 범위를 굵게 해제해줘"
        )
        style = parse_native_excel_range_format_command(
            "엑셀 A1:B3 범위를 글자 크기 12로 하고 글자색 빨간색으로 해줘"
        )
        filtered = parse_native_excel_filter_command(
            "엑셀 상태 열을 완료로 필터해줘"
        )
        sorted_request = parse_native_excel_sort_command(
            "엑셀 매출 열을 내림차순으로 정렬해줘"
        )
        replaced = parse_native_excel_find_replace_command(
            "엑셀 현재 시트에서 서울을 부산으로 바꿔줘"
        )
        self.assertFalse(bold_off["params"]["bold"])
        self.assertEqual("12", style["params"]["font_size"])
        self.assertEqual("빨간색", style["params"]["font_color"])
        self.assertEqual("filter_range", filtered["operation"])
        self.assertEqual("descending", sorted_request["params"]["direction"])
        self.assertEqual("current_sheet", replaced["params"]["scope"])

    def test_every_reversible_operation_has_a_novice_facing_label(self):
        missing = sorted(
            set(ExcelAdapter.undo_supported_operations) - set(ACTION_LABELS)
        )
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
