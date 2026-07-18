import unittest

from engine.edit_mode.target_identity import (
    direct_text_selection_anchor,
    formatting_snapshot,
    single_formatting_change,
)


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

    def test_powerpoint_single_shape_anchor_ignores_text_selection_offsets(self):
        shape = direct_text_selection_anchor("powerpoint", {
            "selection_kind": "shapes",
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "shape_count": 1,
            },
        })
        self.assertEqual(
            {
                "schema_version": 1,
                "kind": "powerpoint_shape_text",
                "slide_id": 256,
                "shape_id": 7,
            },
            shape,
        )
        self.assertIsNone(direct_text_selection_anchor("powerpoint", {
            "selection_kind": "shapes",
            "target": {
                "slide_id": 256,
                "shape_id": 7,
                "shape_count": 2,
            },
        }))

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


class FormattingSnapshotTests(unittest.TestCase):
    @staticmethod
    def _word(**target):
        base = {"bold": 0, "font_size": 11.0, "paragraph_alignment": 0}
        base.update(target)
        return {"selection_kind": "text", "target": base}

    def test_word_snapshot_normalizes_bold_size_and_alignment(self):
        snapshot = formatting_snapshot(
            "word", self._word(bold=-1, font_size=14.0, paragraph_alignment=1)
        )
        self.assertEqual(
            {
                "schema_version": 1,
                "bold": True,
                "font_size": 14.0,
                "alignment": "center",
            },
            snapshot,
        )

    def test_word_and_powerpoint_alignment_integers_map_differently(self):
        word = formatting_snapshot("word", self._word(paragraph_alignment=2))
        powerpoint = formatting_snapshot("powerpoint", {
            "selection_kind": "text",
            "target": {"bold": 0, "font_size": 18.0, "paragraph_alignment": 2},
        })
        self.assertEqual("right", word["alignment"])
        self.assertEqual("center", powerpoint["alignment"])

    def test_powerpoint_single_shape_snapshot_uses_existing_whole_shape_context(self):
        snapshot = formatting_snapshot("powerpoint", {
            "selection_kind": "shapes",
            "target": {
                "shape_count": 1,
                "bold": -1,
                "font_size": 24.0,
                "paragraph_alignment": 2,
            },
        })
        self.assertEqual(
            {
                "schema_version": 1,
                "bold": True,
                "font_size": 24.0,
                "alignment": "center",
            },
            snapshot,
        )
        self.assertIsNone(formatting_snapshot("powerpoint", {
            "selection_kind": "shapes",
            "target": {
                "shape_count": 2,
                "bold": -1,
                "font_size": 24.0,
                "paragraph_alignment": 2,
            },
        }))

    def test_mixed_or_undefined_native_sentinels_normalize_to_none(self):
        snapshot = formatting_snapshot(
            "word",
            self._word(bold=9999999, font_size=9999999.0, paragraph_alignment=99),
        )
        self.assertIsNone(snapshot["bold"])
        self.assertIsNone(snapshot["font_size"])
        self.assertIsNone(snapshot["alignment"])

    def test_cursor_hwp_and_excel_have_no_formatting_snapshot(self):
        self.assertIsNone(formatting_snapshot("word", {
            "selection_kind": "cursor",
            "target": {"bold": 0, "font_size": 11.0},
        }))
        self.assertIsNone(formatting_snapshot("hwp", {
            "selection_kind": "text",
            "target": {"coordinates": [0, 1, 2, 3, 4, 5]},
        }))
        self.assertIsNone(formatting_snapshot("excel", {
            "selection_kind": "range",
            "target": {"address": "A1"},
        }))

    def test_exactly_one_clean_facet_change_maps_to_a_preference(self):
        before = formatting_snapshot("word", self._word())
        bolded = formatting_snapshot("word", self._word(bold=-1))
        larger = formatting_snapshot("word", self._word(font_size=14.0))
        centered = formatting_snapshot("word", self._word(paragraph_alignment=1))
        self.assertEqual(
            ("emphasis_style", "bold"), single_formatting_change(before, bolded)
        )
        self.assertEqual(
            ("font_scale", "larger"), single_formatting_change(before, larger)
        )
        self.assertEqual(
            ("paragraph_align", "center"),
            single_formatting_change(before, centered),
        )
        self.assertEqual(
            ("emphasis_style", "regular"),
            single_formatting_change(bolded, before),
        )

    def test_multi_facet_tiny_or_ambiguous_changes_are_not_interpreted(self):
        before = formatting_snapshot("word", self._word())
        multi = formatting_snapshot(
            "word", self._word(bold=-1, font_size=14.0)
        )
        nudged = formatting_snapshot("word", self._word(font_size=11.5))
        undefined = formatting_snapshot("word", self._word(bold=9999999))
        self.assertIsNone(single_formatting_change(before, multi))
        self.assertIsNone(single_formatting_change(before, nudged))
        self.assertIsNone(single_formatting_change(before, undefined))
        self.assertIsNone(single_formatting_change(None, before))


if __name__ == "__main__":
    unittest.main()
