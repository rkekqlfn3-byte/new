"""Safe conversion and comparison helpers for native Excel values."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal

from engine.app_actions.base import AppActionBlocked

INTEGER_RE = re.compile(r"^[+-]?\d+$")
DECIMAL_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?$")
CELL_RE = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9]\d{0,6})$", re.IGNORECASE)
SINGLE_COLUMN_RANGE_RE = re.compile(
    r"^\$?([A-Z]{1,3})\$?([1-9]\d{0,6}):\$?([A-Z]{1,3})\$?([1-9]\d{0,6})$",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"^\$?([A-Z]{1,3})\$?([1-9]\d{0,6}):\$?([A-Z]{1,3})\$?([1-9]\d{0,6})$",
    re.IGNORECASE,
)


def normalize_cell_address(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    match = CELL_RE.fullmatch(text)
    if not match:
        raise AppActionBlocked("셀 주소는 A1 같은 단일 셀 형식이어야 합니다.")
    column, row_text = match.groups()
    column_number = 0
    for character in column:
        column_number = column_number * 26 + ord(character) - 64
    row = int(row_text)
    if column_number > 16384 or row > 1048576:
        raise AppActionBlocked("Excel에서 사용할 수 없는 셀 주소입니다.")
    return f"{column}{row}"


def excel_column_number(column: str) -> int:
    text = str(column or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", text):
        raise AppActionBlocked("Excel 열은 A 또는 AA 같은 형식이어야 합니다.")
    number = 0
    for character in text:
        number = number * 26 + ord(character) - 64
    if number > 16384:
        raise AppActionBlocked("Excel에서 사용할 수 없는 열입니다.")
    return number


def excel_column_letters(number: int) -> str:
    value = int(number)
    if value < 1 or value > 16384:
        raise AppActionBlocked("Excel에서 사용할 수 없는 열 번호입니다.")
    letters = []
    while value:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr(65 + remainder))
    return "".join(reversed(letters))


def normalize_single_column_range(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    match = SINGLE_COLUMN_RANGE_RE.fullmatch(text)
    if not match:
        raise AppActionBlocked("범위는 A2:A10 같은 단일 열 형식이어야 합니다.")
    first_column, first_row_text, last_column, last_row_text = match.groups()
    if first_column != last_column:
        raise AppActionBlocked("4단계에서는 한 열로 된 범위만 지원합니다.")
    excel_column_number(first_column)
    first_row = int(first_row_text)
    last_row = int(last_row_text)
    if first_row > 1048576 or last_row > 1048576 or first_row > last_row:
        raise AppActionBlocked("Excel 범위의 시작·끝 행을 확인해주세요.")
    return f"{first_column}{first_row}:{last_column}{last_row}"


def normalize_range_address(value: object, allow_single_cell=True) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if allow_single_cell and CELL_RE.fullmatch(text):
        return normalize_cell_address(text)
    match = RANGE_RE.fullmatch(text)
    if not match:
        raise AppActionBlocked("범위는 A2:C10 같은 직사각형 형식이어야 합니다.")
    first_column, first_row_text, last_column, last_row_text = match.groups()
    first_column_number = excel_column_number(first_column)
    last_column_number = excel_column_number(last_column)
    first_row = int(first_row_text)
    last_row = int(last_row_text)
    if first_row > 1048576 or last_row > 1048576:
        raise AppActionBlocked("Excel에서 사용할 수 없는 행 번호입니다.")
    if first_column_number > last_column_number or first_row > last_row:
        raise AppActionBlocked("Excel 범위의 왼쪽 위와 오른쪽 아래 순서를 확인해주세요.")
    return f"{first_column}{first_row}:{last_column}{last_row}"


def normalize_excel_input(value: object, value_type: str | None = None) -> dict:
    requested_type = str(value_type or "auto").strip().lower()
    if requested_type not in {"auto", "value", "formula", "text", "number"}:
        raise AppActionBlocked(f"지원하지 않는 Excel 입력 형식입니다: {value_type}")

    if isinstance(value, bool):
        return {"kind": "value", "value": value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise AppActionBlocked("무한대나 NaN은 Excel 셀에 입력할 수 없습니다.")
        return {"kind": "value", "value": value}

    text = str(value if value is not None else "").strip()
    if requested_type == "formula" or (requested_type == "auto" and text.startswith("=")):
        if not text.startswith("="):
            text = "=" + text
        if len(text) > 8192 or any(character in text for character in "\r\n\x00"):
            raise AppActionBlocked("Excel 수식이 너무 길거나 올바르지 않습니다.")
        return {"kind": "formula", "value": text}

    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
        requested_type = "text"
    if requested_type == "text":
        return {"kind": "value", "value": text}

    compact = text.replace(",", "")
    if requested_type in {"auto", "number", "value"} and INTEGER_RE.fullmatch(compact):
        # Excel numbers retain at most 15 significant digits. Preserve longer IDs as text.
        digits = compact.lstrip("+-").lstrip("0") or "0"
        if len(digits) <= 15:
            return {"kind": "value", "value": int(compact)}
        if requested_type == "number":
            raise AppActionBlocked("15자리를 넘는 숫자는 정확도를 잃을 수 있어 숫자로 입력하지 않았습니다.")
    if requested_type in {"auto", "number", "value"} and DECIMAL_RE.fullmatch(compact):
        number = float(compact)
        if math.isfinite(number):
            return {"kind": "value", "value": number}
    if requested_type == "number":
        raise AppActionBlocked("숫자로 해석할 수 없는 입력값입니다.")
    return {"kind": "value", "value": text}


def serializable_excel_value(value: object):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [serializable_excel_value(item) for item in value]
    return str(value)


def excel_values_equal(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    return serializable_excel_value(actual) == serializable_excel_value(expected)


def normalize_excel_formula_for_compare(value: object) -> str:
    """Normalize only syntax-insignificant formula differences.

    Whitespace and character case outside quoted string literals do not change
    an Excel formula. Absolute references, operators, separators, and literal
    contents remain untouched so a materially different formula never passes.
    """
    text = str(value or "").strip()
    normalized = []
    in_string = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            normalized.append(character)
            if in_string and index + 1 < len(text) and text[index + 1] == '"':
                normalized.append('"')
                index += 2
                continue
            in_string = not in_string
        elif not in_string and character.isspace():
            index += 1
            continue
        elif in_string:
            normalized.append(character)
        else:
            normalized.append(character.upper())
        index += 1
    return "".join(normalized)


def excel_formulas_equal(actual: object, expected: object) -> bool:
    return normalize_excel_formula_for_compare(actual) == normalize_excel_formula_for_compare(expected)
