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


# Word wdParagraphAlignment and PowerPoint ppParagraphAlignment use
# different integers for the same visual result.
_WORD_ALIGNMENTS = {0: "left", 1: "center", 2: "right", 3: "justify"}
_POWERPOINT_ALIGNMENTS = {1: "left", 2: "center", 3: "right", 4: "justify"}


def _clean_bold(value) -> bool | None:
    """Accept only unambiguous bold states; mixed/undefined sentinels drop out."""
    if isinstance(value, bool):
        return value
    number = _integer(value)
    if number == 0:
        return False
    if number in (1, -1):
        return True
    return None


def _clean_font_size(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 1.0 <= number <= 500.0:
        return None
    return round(number, 1)


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


def formatting_snapshot(app_type: str, context: Mapping) -> dict | None:
    """Return normalized text formatting for one Word/PowerPoint text target.

    The snapshot holds only structured scalars (bold flag, point size,
    alignment label).  HWP context does not expose formatting, and mixed or
    undefined native sentinels normalize to ``None`` so a later comparison
    never treats them as a real change.
    """
    normalized = str(app_type or context.get("app_type") or "").casefold()
    kind = str(context.get("selection_kind") or "").casefold()
    target = context.get("target") or {}
    if not isinstance(target, Mapping):
        return None
    if normalized == "word" and kind in {"text", "table_cell"}:
        alignments = _WORD_ALIGNMENTS
    elif normalized == "powerpoint" and kind == "text":
        alignments = _POWERPOINT_ALIGNMENTS
    else:
        return None
    return {
        "schema_version": 1,
        "bold": _clean_bold(target.get("bold")),
        "font_size": _clean_font_size(target.get("font_size")),
        "alignment": alignments.get(_integer(target.get("paragraph_alignment"))),
    }


def single_formatting_change(before, after) -> tuple[str, str] | None:
    """Map exactly one clean formatting facet change to a preference value.

    Ambiguous states (``None`` on either side) and simultaneous multi-facet
    changes return ``None`` so noisy edits are never guessed.
    """
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    changes: list[tuple[str, str]] = []
    previous_bold = before.get("bold")
    current_bold = after.get("bold")
    if (
        isinstance(previous_bold, bool)
        and isinstance(current_bold, bool)
        and previous_bold != current_bold
    ):
        changes.append(("emphasis_style", "bold" if current_bold else "regular"))
    previous_size = before.get("font_size")
    current_size = after.get("font_size")
    if (
        isinstance(previous_size, (int, float))
        and isinstance(current_size, (int, float))
        and not isinstance(previous_size, bool)
        and not isinstance(current_size, bool)
        and abs(float(current_size) - float(previous_size)) >= 1.0
    ):
        changes.append(
            ("font_scale", "larger" if current_size > previous_size else "smaller")
        )
    previous_alignment = before.get("alignment")
    current_alignment = after.get("alignment")
    if (
        isinstance(previous_alignment, str)
        and isinstance(current_alignment, str)
        and previous_alignment != current_alignment
    ):
        changes.append(("paragraph_align", current_alignment))
    if len(changes) != 1:
        return None
    return changes[0]
