"""The one place page margin and orientation wording is defined.

Margins are expressed in millimetres, which is how Korean office templates
prescribe them and how both 한글 and Word accept them after conversion.
"""

from __future__ import annotations

import re

PORTRAIT = "portrait"
LANDSCAPE = "landscape"

ORIENTATIONS = (PORTRAIT, LANDSCAPE)

ORIENTATION_ALIASES = {
    "세로": PORTRAIT, "세로 방향": PORTRAIT, "세로로": PORTRAIT,
    "portrait": PORTRAIT,
    "가로": LANDSCAPE, "가로 방향": LANDSCAPE, "가로로": LANDSCAPE,
    "landscape": LANDSCAPE,
}

MARGIN_SIDES = ("left", "right", "top", "bottom")
MARGIN_ALIASES = {
    "왼쪽": "left", "좌": "left", "left": "left",
    "오른쪽": "right", "우": "right", "right": "right",
    "위": "top", "위쪽": "top", "상": "top", "top": "top",
    "아래": "bottom", "아래쪽": "bottom", "하": "bottom", "bottom": "bottom",
}

MIN_MARGIN_MM = 0
MAX_MARGIN_MM = 100

ORIENTATION_COMMAND_PATTERN = r"(가로|세로)\s*(?:방향|용지|으로|로)"
MARGIN_COMMAND_PATTERN = r"여백"

_MARGIN_VALUE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:mm|밀리|미리)?")

_KOREAN_ORIENTATIONS = {PORTRAIT: "세로 방향", LANDSCAPE: "가로 방향"}


def normalize_orientation(value):
    text = str(value or "").strip().casefold()
    canonical = ORIENTATION_ALIASES.get(text, text)
    if canonical not in ORIENTATIONS:
        raise ValueError("용지 방향은 세로 또는 가로를 지원합니다.")
    return canonical


def orientation_label(canonical) -> str:
    return _KOREAN_ORIENTATIONS[canonical]


def normalize_margin_mm(value):
    """Resolve `20`, `"20"` or `"20mm"` to a whole millimetre count."""
    if value is None:
        raise ValueError("여백 값을 지정해주세요.")
    if isinstance(value, bool):
        raise ValueError("여백은 숫자로 지정해주세요.")
    if isinstance(value, (int, float)):
        millimetres = float(value)
    else:
        match = _MARGIN_VALUE_RE.search(str(value).strip())
        if not match:
            raise ValueError("여백은 20 또는 20mm처럼 숫자로 지정해주세요.")
        millimetres = float(match.group(1))
    millimetres = round(millimetres)
    if not MIN_MARGIN_MM <= millimetres <= MAX_MARGIN_MM:
        raise ValueError(
            f"여백은 {MIN_MARGIN_MM}mm부터 {MAX_MARGIN_MM}mm 사이여야 합니다."
        )
    return int(millimetres)


def normalize_margin_sides(value):
    """Which sides a request names; empty means every side."""
    text = str(value or "").strip().casefold()
    if not text:
        return tuple(MARGIN_SIDES)
    sides = [MARGIN_ALIASES[word] for word in MARGIN_ALIASES if word in text]
    ordered = tuple(side for side in MARGIN_SIDES if side in sides)
    return ordered or tuple(MARGIN_SIDES)


__all__ = [
    "LANDSCAPE",
    "MARGIN_ALIASES",
    "MARGIN_COMMAND_PATTERN",
    "MARGIN_SIDES",
    "MAX_MARGIN_MM",
    "MIN_MARGIN_MM",
    "ORIENTATIONS",
    "ORIENTATION_ALIASES",
    "ORIENTATION_COMMAND_PATTERN",
    "PORTRAIT",
    "normalize_margin_mm",
    "normalize_margin_sides",
    "normalize_orientation",
    "orientation_label",
]
