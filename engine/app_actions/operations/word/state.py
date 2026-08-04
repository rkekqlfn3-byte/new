"""Word constants and helpers shared by more than one operation."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked
from engine.vocabulary.alignment import normalize_alignment as _normalize

WD_ALIGNMENTS = {
    "left": 0,
    "center": 1,
    "right": 2,
    "justify": 3,
}


def normalize_alignment(value):
    """Return the canonical name and Word's own constant."""
    numeric = {index: name for name, index in WD_ALIGNMENTS.items()}
    try:
        if int(value) in numeric and str(value).strip() == str(int(value)):
            name = numeric[int(value)]
            return name, WD_ALIGNMENTS[name]
    except (TypeError, ValueError):
        pass
    try:
        name = _normalize(value, WD_ALIGNMENTS, "Word")
    except ValueError as error:
        raise AppActionBlocked(str(error)) from error
    return name, WD_ALIGNMENTS[name]


def apply_text_format(target, desired):
    """Apply a prepared font change. Also used when restoring the original."""
    if "bold" in desired:
        target.Font.Bold = desired["bold"]
    if "font_size" in desired:
        target.Font.Size = desired["font_size"]


__all__ = ["WD_ALIGNMENTS", "apply_text_format", "normalize_alignment"]
