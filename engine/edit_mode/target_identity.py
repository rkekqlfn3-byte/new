"""Content-free structural identity helpers for native edit targets."""

from __future__ import annotations

from collections.abc import Mapping


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def direct_text_selection_anchor(app_type: str, context: Mapping) -> dict | None:
    """Return a JSON-only anchor whose end offset may safely change.

    The anchor deliberately contains no selected text, preview, digest, or length.
    It is used only after a verified JARVIS text edit to recognize a user's direct
    shortening of the same structural selection.
    """
    normalized = str(app_type or context.get("app_type") or "").casefold()
    kind = str(context.get("selection_kind") or "").casefold()
    target = context.get("target") or {}
    if not isinstance(target, Mapping):
        return None

    if normalized == "word" and kind in {"text", "table_cell"}:
        start = _integer(target.get("start"))
        if start is None or start < 0:
            return None
        anchor = {
            "schema_version": 1,
            "kind": "word_text",
            "start": start,
        }
        if kind == "table_cell":
            table_start = _integer(target.get("table_start"))
            row = _integer(target.get("table_row"))
            column = _integer(target.get("table_column"))
            if table_start is None or row is None or column is None:
                return None
            anchor.update({
                "table_start": table_start,
                "table_row": row,
                "table_column": column,
            })
        return anchor

    if normalized == "powerpoint" and kind == "text":
        slide_id = _integer(target.get("slide_id"))
        shape_id = _integer(target.get("shape_id"))
        text_start = _integer(target.get("text_start"))
        if (
            slide_id is None
            or slide_id <= 0
            or shape_id is None
            or shape_id <= 0
            or text_start is None
            or text_start < 0
        ):
            return None
        return {
            "schema_version": 1,
            "kind": "powerpoint_text",
            "slide_id": slide_id,
            "shape_id": shape_id,
            "text_start": text_start,
        }

    if normalized == "hwp" and kind == "text":
        coordinates = target.get("coordinates") or []
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 6:
            return None
        values = [_integer(value) for value in coordinates[:6]]
        if any(value is None for value in values):
            return None
        first = tuple(values[:3])
        second = tuple(values[3:6])
        start = min(first, second)
        return {
            "schema_version": 1,
            "kind": "hwp_text",
            "start": list(start),
        }

    return None
