import unittest

from engine.app_actions.base import AppActionBlocked
from engine.app_actions.office_helpers import (
    count_hwp_matches,
    excel_format_state_matches,
    excel_range_bounds,
    replace_excel_text,
    replace_hwp_text,
    stable_state_fingerprint,
)


class OfficeHelperTests(unittest.TestCase):
    def test_fingerprint_is_stable_across_dictionary_order(self):
        self.assertEqual(
            stable_state_fingerprint({"sheet": "시트1", "row": 3}),
            stable_state_fingerprint({"row": 3, "sheet": "시트1"}),
        )

    def test_excel_range_bounds_accepts_single_cell_and_range(self):
        self.assertEqual(excel_range_bounds("$B$2"), (2, 2, 2, 2))
        self.assertEqual(excel_range_bounds("A1:C10"), (1, 1, 3, 10))

    def test_excel_range_bounds_rejects_invalid_address(self):
        with self.assertRaises(AppActionBlocked):
            excel_range_bounds("not-a-range")

    def test_excel_format_comparison_normalizes_bold_and_font_size(self):
        state = {"bold": 1, "font_size": 10.0, "fill_color": 255}
        self.assertTrue(
            excel_format_state_matches(
                state, {"bold": True, "font_size": "10", "fill_color": 255}
            )
        )
        self.assertFalse(excel_format_state_matches(state, {"fill_color": 0}))

    def test_excel_replacement_preserves_whole_cell_semantics(self):
        self.assertEqual(
            replace_excel_text("Alpha", "alpha", "Beta", True, False), "Beta"
        )
        self.assertIsNone(
            replace_excel_text("Alpha suffix", "alpha", "Beta", True, False)
        )
        self.assertEqual(
            replace_excel_text("Alpha suffix", "alpha", "Beta", False, False),
            "Beta suffix",
        )

    def test_hwp_replacement_and_count_share_case_rule(self):
        self.assertEqual(count_hwp_matches("A a B", "a", False), 2)
        self.assertEqual(replace_hwp_text("A a B", "a", "x", False), "x x B")
        self.assertEqual(count_hwp_matches("A a B", "a", True), 1)


if __name__ == "__main__":
    unittest.main()
