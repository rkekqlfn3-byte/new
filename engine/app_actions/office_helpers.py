"""COM-free helpers shared by native Office application adapters."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime

from engine.app_actions.base import AppActionBlocked
from engine.app_actions.value_normalizer import excel_column_number


def prepared_at_timestamp() -> str:
    """Return the common timestamp format stored in ``PreparedAction``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stable_state_fingerprint(snapshot: dict) -> str:
    """Create a stable fingerprint without touching an Office COM object."""
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def excel_range_bounds(address: str) -> tuple[int, int, int, int]:
    """Parse an A1 range into numeric inclusive bounds."""
    text = str(address or "").replace("$", "").upper()
    match = re.fullmatch(
        r"([A-Z]{1,3})(\d+)(?::([A-Z]{1,3})(\d+))?", text
    )
    if not match:
        raise AppActionBlocked("Excel 범위 주소를 해석하지 못했습니다.")
    first_column, first_row, last_column, last_row = match.groups()
    return (
        excel_column_number(first_column),
        int(first_row),
        excel_column_number(last_column or first_column),
        int(last_row or first_row),
    )


def excel_format_state_matches(state: dict, desired: dict) -> bool:
    """Compare a serializable Excel format snapshot with desired values."""
    for key, value in desired.items():
        if key == "font_size":
            try:
                if math.isclose(float(state.get(key)), float(value)):
                    continue
            except (TypeError, ValueError):
                pass
            return False
        if key == "bold":
            if bool(state.get(key)) != bool(value):
                return False
        elif state.get(key) != value:
            return False
    return True


def replace_excel_text(
    value: str,
    old: str,
    new: str,
    whole_cell: bool,
    match_case: bool,
) -> str | None:
    """Return an Excel replacement value, or ``None`` when nothing matches."""
    if whole_cell:
        matches = value == old if match_case else value.casefold() == old.casefold()
        return new if matches else None
    flags = 0 if match_case else re.IGNORECASE
    replaced, count = re.subn(re.escape(old), lambda _: new, value, flags=flags)
    return replaced if count else None


def replace_hwp_text(
    value: str, find: str, replace: str, match_case: bool
) -> str:
    """Apply the literal replacement semantics used by the HWP adapter."""
    flags = 0 if match_case else re.IGNORECASE
    return re.sub(re.escape(find), lambda _: replace, value, flags=flags)


def count_hwp_matches(value: str, find: str, match_case: bool) -> int:
    """Count literal HWP replacement matches using the same case rule."""
    flags = 0 if match_case else re.IGNORECASE
    return len(re.findall(re.escape(find), value, flags=flags))

