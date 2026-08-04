"""The one place Korean alignment wording is defined.

This used to live in seven places — two intent analysers, two command
parsers and one per application adapter — and they had drifted.  `좌측 정렬`
worked through the Excel command path but was not recognised in edit mode at
all, and `배분` only reached 한글.  Same request, different answer depending on
which route it took, which is a direct counterexample to the product Goal's
first criterion.

Applications differ in which alignments they *support*, not in how a user is
allowed to say them.  So the alias table is shared and each adapter passes the
set it can actually apply.
"""

from __future__ import annotations

import re

LEFT = "left"
CENTER = "center"
RIGHT = "right"
JUSTIFY = "justify"

CANONICAL_ALIGNMENTS = (LEFT, CENTER, RIGHT, JUSTIFY)

# Every accepted spoken form. Keys are casefolded; callers casefold first.
ALIGNMENT_ALIASES = {
    "왼쪽": LEFT, "왼쪽 정렬": LEFT, "좌측": LEFT, "좌측 정렬": LEFT,
    "center": CENTER, "가운데": CENTER, "가운데 정렬": CENTER,
    "중앙": CENTER, "중앙 정렬": CENTER,
    "오른쪽": RIGHT, "오른쪽 정렬": RIGHT, "우측": RIGHT, "우측 정렬": RIGHT,
    "양쪽": JUSTIFY, "양쪽 정렬": JUSTIFY, "배분": JUSTIFY, "배분 정렬": JUSTIFY,
    LEFT: LEFT, RIGHT: RIGHT, JUSTIFY: JUSTIFY,
}

# Bare words an intent analyser may see before the trailing `정렬`.
ALIGNMENT_WORDS = tuple(
    sorted(
        {alias for alias in ALIGNMENT_ALIASES if not alias.endswith(" 정렬")},
        key=len,
        reverse=True,
    )
)

_KOREAN_LABELS = {
    LEFT: "왼쪽",
    CENTER: "가운데",
    RIGHT: "오른쪽",
    JUSTIFY: "양쪽",
}

# `(왼쪽|좌측|…)(?:으로)?\s*정렬`, built from the table so a new alias reaches
# the intent analysers without another edit.
ALIGNMENT_COMMAND_PATTERN = (
    r"(" + "|".join(re.escape(word) for word in ALIGNMENT_WORDS) + r")"
    r"(?:으로)?\s*정렬"
)


def korean_label(canonical, suffix="") -> str:
    """The word shown back to the user for one canonical alignment."""
    return _KOREAN_LABELS[canonical] + suffix


def korean_labels(suffix="") -> dict:
    """``{"left": "왼쪽 정렬", …}`` for preference and confirmation copy."""
    return {name: korean_label(name, suffix) for name in CANONICAL_ALIGNMENTS}


def supported_label(supported) -> str:
    """`왼쪽·가운데·오른쪽` for the set an application can actually apply."""
    ordered = [
        _KOREAN_LABELS[name] for name in CANONICAL_ALIGNMENTS if name in supported
    ]
    return "·".join(ordered)


def normalize_alignment(value, supported=CANONICAL_ALIGNMENTS, app_label=""):
    """Resolve any accepted wording to one canonical alignment.

    Raises ``ValueError`` when the wording is unknown or the application does
    not support that alignment; adapters translate it into their own blocked
    error so the message stays in their voice.
    """
    text = str(value or "").strip().casefold()
    canonical = ALIGNMENT_ALIASES.get(text, text)
    prefix = f"{app_label} " if app_label else ""
    if canonical not in supported:
        raise ValueError(
            f"{prefix}정렬은 {supported_label(supported)} 정렬을 지원합니다."
        )
    return canonical


__all__ = [
    "ALIGNMENT_ALIASES",
    "ALIGNMENT_COMMAND_PATTERN",
    "ALIGNMENT_WORDS",
    "CANONICAL_ALIGNMENTS",
    "CENTER",
    "JUSTIFY",
    "LEFT",
    "RIGHT",
    "normalize_alignment",
    "supported_label",
]
