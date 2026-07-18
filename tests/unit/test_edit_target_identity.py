import unittest

from engine.edit_mode.target_identity import direct_text_selection_anchor


class DirectTextSelectionAnchorTests(unittest.TestCase):
    def test_word_end_offset_may_change_but_start_may_not(self):
        before = direct_text_selection_anchor("word", {
            "selection_kind": "text",
            "target": {"start": 10, "end": 110},
        })
        shorter = direct_text_selection_anchor("word", {
            "selection_kind": "text",
            "target": {"start": 10, "end": 70},
        })
        moved = direct_text_selection_anchor("word", {
            "selection_kind": "text",
            "target": {"start": 20, "end": 80},
        })
        self.assertEqual(before, shorter)
        self.assertNotEqual(before, moved)

    def test_word_table_anchor_includes_table_and_cell_identity(self):
        anchor = direct_text_selection_anchor("word", {
            "selection_kind": "table_cell",
            "target": {
                "start": 25,
                "end": 80,
                "table_start": 20,
                "table_row": 2,
                "table_column": 3,
            },
        })
        self.assertEqual(20, anchor["table_start"])
        self.assertEqual(2, anchor["table_row"])
        self.assertEqual(3, anchor["table_column"])

    def test_powerpoint_anchor_uses_stable_slide_shape_and_text_start(self):
        before = direct_text_selection_anchor("powerpoint", {
            "selection_kind": "text",
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "text_start": 1,
                "text_end": 101,
            },
        })
        shorter = direct_text_selection_anchor("powerpoint", {
            "selection_kind": "text",
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "text_start": 1,
                "text_end": 61,
            },
        })
        other_shape = direct_text_selection_anchor("powerpoint", {
            "selection_kind": "text",
            "target": {
                "slide_id": 256,
                "shape_id": 8,
                "text_start": 1,
                "text_end": 61,
            },
        })
        self.assertEqual(before, shorter)
        self.assertNotEqual(before, other_shape)

    def test_hwp_anchor_normalizes_selection_direction_and_ignores_end(self):
        before = direct_text_selection_anchor("hwp", {
            "selection_kind": "text",
            "target": {"coordinates": [0, 2, 10, 0, 2, 110]},
        })
        reverse_shorter = direct_text_selection_anchor("hwp", {
            "selection_kind": "text",
            "target": {"coordinates": [0, 2, 70, 0, 2, 10]},
        })
        self.assertEqual(before, reverse_shorter)

    def test_cursor_excel_and_incomplete_targets_have_no_text_anchor(self):
        self.assertIsNone(direct_text_selection_anchor("word", {
            "selection_kind": "cursor",
            "target": {"start": 10, "end": 10},
        }))
        self.assertIsNone(direct_text_selection_anchor("excel", {
            "selection_kind": "range",
            "target": {"address": "A1"},
        }))
        self.assertIsNone(direct_text_selection_anchor("powerpoint", {
            "selection_kind": "text",
            "target": {"slide_id": 1, "text_start": 1},
        }))


if __name__ == "__main__":
    unittest.main()
