"""PowerPoint constants and validators shared by more than one operation."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked

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
    numeric = {
        1: ("left", 1),
        2: ("center", 2),
        3: ("right", 3),
        4: ("justify", 4),
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
    if text not in PPT_ALIGNMENTS:
        raise AppActionBlocked(
            "PowerPoint 정렬은 왼쪽·가운데·오른쪽·양쪽을 지원합니다."
        )
    return text, PPT_ALIGNMENTS[text]


__all__ = ["PPT_ALIGNMENTS", "normalize_alignment", "uniform_style_value"]
