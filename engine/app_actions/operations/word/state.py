"""Word constants and helpers shared by more than one operation."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked

WD_ALIGNMENTS = {
    "left": 0,
    "center": 1,
    "right": 2,
    "justify": 3,
}


def normalize_alignment(value):
    numeric = {
        0: ("left", 0),
        1: ("center", 1),
        2: ("right", 2),
        3: ("justify", 3),
    }
    try:
        if int(value) in numeric and str(value).strip() == str(int(value)):
            return numeric[int(value)]
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip().casefold()
    aliases = {
        "왼쪽": "left",
        "가운데": "center",
        "중앙": "center",
        "오른쪽": "right",
        "양쪽": "justify",
    }
    text = aliases.get(text, text)
    if text not in WD_ALIGNMENTS:
        raise AppActionBlocked("Word 정렬은 왼쪽·가운데·오른쪽·양쪽을 지원합니다.")
    return text, WD_ALIGNMENTS[text]


def apply_text_format(target, desired):
    """Apply a prepared font change. Also used when restoring the original."""
    if "bold" in desired:
        target.Font.Bold = desired["bold"]
    if "font_size" in desired:
        target.Font.Size = desired["font_size"]


__all__ = ["WD_ALIGNMENTS", "apply_text_format", "normalize_alignment"]
