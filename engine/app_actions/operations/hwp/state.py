"""Live Hanword state readers shared by more than one operation.

These read the active document's character and paragraph shape.  They live
here rather than inside one operation because ``insert_text`` reports the
resulting format, ``set_text_format`` and ``set_paragraph_format`` own it, and
``HwpUndoService`` reads it back while restoring.
"""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked

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


def paragraph_state(hwp):
    shape = hwp.HParameterSet.HParaShape
    hwp.HAction.GetDefault("ParagraphShape", shape.HSet)
    return {"alignment": int(shape.AlignType)}


def normalize_alignment(value):
    alignment = str(value or "").strip().casefold()
    aliases = {
        "양쪽": "justify", "양쪽 정렬": "justify", "배분": "justify",
        "왼쪽": "left", "왼쪽 정렬": "left", "좌측": "left",
        "오른쪽": "right", "오른쪽 정렬": "right", "우측": "right",
        "가운데": "center", "가운데 정렬": "center", "중앙": "center",
    }
    alignment = aliases.get(alignment, alignment)
    if alignment not in PARAGRAPH_ALIGNMENTS:
        raise AppActionBlocked("문단 정렬은 왼쪽·가운데·오른쪽·양쪽 정렬을 지원합니다.")
    return alignment


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
    "PARAGRAPH_ALIGNMENTS",
    "char_state",
    "normalize_alignment",
    "normalize_color_name",
    "paragraph_state",
]
