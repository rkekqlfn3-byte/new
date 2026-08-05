"""The one place table sizing wording is defined."""

from __future__ import annotations

import re

MIN_COLUMN_WIDTH_MM = 5
MAX_COLUMN_WIDTH_MM = 250

COLUMN_WIDTH_COMMAND_PATTERN = r"(?:열\s*(?:너비|폭)|칸\s*(?:너비|폭)|너비|폭)"

_VALUE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:mm|밀리|미리)?")


def normalize_column_width_mm(value):
    """Resolve ``40``, ``"40"`` or ``"40mm"`` to a whole millimetre count."""
    if value is None:
        raise ValueError("열 너비를 지정해주세요.")
    if isinstance(value, bool):
        raise ValueError("열 너비는 숫자로 지정해주세요.")
    if isinstance(value, (int, float)):
        millimetres = float(value)
    else:
        match = _VALUE_RE.search(str(value).strip())
        if not match:
            raise ValueError("열 너비는 40 또는 40mm처럼 숫자로 지정해주세요.")
        millimetres = float(match.group(1))
    millimetres = round(millimetres)
    if not MIN_COLUMN_WIDTH_MM <= millimetres <= MAX_COLUMN_WIDTH_MM:
        raise ValueError(
            f"열 너비는 {MIN_COLUMN_WIDTH_MM}mm부터 {MAX_COLUMN_WIDTH_MM}mm "
            "사이여야 합니다."
        )
    return int(millimetres)


def column_width_label(millimetres) -> str:
    return f"열 너비 {int(millimetres)}mm"


__all__ = [
    "COLUMN_WIDTH_COMMAND_PATTERN",
    "MAX_COLUMN_WIDTH_MM",
    "MIN_COLUMN_WIDTH_MM",
    "column_width_label",
    "normalize_column_width_mm",
]
