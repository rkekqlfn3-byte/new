"""Live Hanword state readers shared by more than one operation.

These read the active document's character and paragraph shape.  They live
here rather than inside one operation because ``insert_text`` reports the
resulting format, ``set_text_format`` and ``set_paragraph_format`` own it, and
``HwpUndoService`` reads it back while restoring.
"""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked
from engine.vocabulary.alignment import normalize_alignment as _normalize

PARAGRAPH_ALIGNMENTS = {
    "justify": ("ParagraphShapeAlignJustify", 0),
    "left": ("ParagraphShapeAlignLeft", 1),
    "right": ("ParagraphShapeAlignRight", 2),
    "center": ("ParagraphShapeAlignCenter", 3),
}

COLOR_RGB = {
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128),
}


def char_state(hwp):
    shape = hwp.HParameterSet.HCharShape
    hwp.HAction.GetDefault("CharShape", shape.HSet)
    return {
        "bold": int(shape.Bold),
        "font_size_hu": int(shape.Height),
        "text_color": int(shape.TextColor),
    }


# 0 = percent of line height, which is the only mode Jarvis sets. Fixed-value
# and minimum-spacing modes are read back but never written.
LINE_SPACING_PERCENT_TYPE = 0


def paragraph_state(hwp):
    shape = hwp.HParameterSet.HParaShape
    hwp.HAction.GetDefault("ParagraphShape", shape.HSet)
    state = {"alignment": int(shape.AlignType)}
    spacing = getattr(shape, "LineSpacing", None)
    if spacing is not None:
        state["line_spacing"] = int(spacing)
        state["line_spacing_type"] = int(
            getattr(shape, "LineSpacingType", LINE_SPACING_PERCENT_TYPE)
        )
    return state


def normalize_alignment(value):
    try:
        return _normalize(value, PARAGRAPH_ALIGNMENTS, "문단")
    except ValueError as error:
        raise AppActionBlocked(str(error)) from error


def normalize_color_name(value):
    color = str(value or "").strip().casefold()
    aliases = {
        "검은색": "black", "검정": "black",
        "빨간색": "red", "빨강": "red",
        "초록색": "green", "녹색": "green", "초록": "green",
        "파란색": "blue", "파랑": "blue",
        "노란색": "yellow", "노랑": "yellow",
        "주황색": "orange", "주황": "orange",
        "회색": "gray",
    }
    color = aliases.get(color, color)
    if color not in COLOR_RGB:
        raise AppActionBlocked(
            "한글 글자색은 검정·빨강·초록·파랑·노랑·주황·회색을 지원합니다."
        )
    return color


__all__ = [
    "COLOR_RGB",
    "LINE_SPACING_PERCENT_TYPE",
    "PARAGRAPH_ALIGNMENTS",
    "char_state",
    "normalize_alignment",
    "normalize_color_name",
    "paragraph_state",
]
