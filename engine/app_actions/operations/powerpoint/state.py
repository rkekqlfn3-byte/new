"""PowerPoint constants and validators shared by more than one operation."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked
from engine.vocabulary.alignment import normalize_alignment as _normalize

PPT_ALIGNMENTS = {
    "left": 1,
    "center": 2,
    "right": 3,
    "justify": 4,
}


def uniform_style_value(value, label):
    """Reject a mixed selection before changing anything.

    PowerPoint reports a sentinel when the selected text has more than one
    value for an attribute; changing it would silently flatten the rest.
    """
    if value is None:
        raise AppActionBlocked(f"PowerPoint {label}을 읽지 못했습니다.")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise AppActionBlocked(f"PowerPoint {label}을 읽지 못했습니다.") from error
    if number <= -2 or abs(number) >= 9_999_999:
        raise AppActionBlocked(
            f"선택 텍스트의 PowerPoint {label}이 서로 달라 변경하지 않습니다."
        )
    return number


def normalize_alignment(value):
    """Return the canonical name and PowerPoint's own constant."""
    numeric = {index: name for name, index in PPT_ALIGNMENTS.items()}
    try:
        if int(value) in numeric and str(value).strip() == str(int(value)):
            name = numeric[int(value)]
            return name, PPT_ALIGNMENTS[name]
    except (TypeError, ValueError):
        pass
    try:
        name = _normalize(value, PPT_ALIGNMENTS, "PowerPoint")
    except ValueError as error:
        raise AppActionBlocked(str(error)) from error
    return name, PPT_ALIGNMENTS[name]


__all__ = ["PPT_ALIGNMENTS", "normalize_alignment", "uniform_style_value"]
