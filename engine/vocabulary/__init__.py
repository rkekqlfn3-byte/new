"""Shared Korean wording, defined once.

A concept that users speak — an alignment, a way to say no — belongs here, so
adding a synonym is one edit rather than a hunt through parsers, intent
analysers and adapters.  ``verification.maintenance_audit`` fails the build if
one of these tables is copied back out.
"""

from __future__ import annotations

from engine.vocabulary.alignment import (
    ALIGNMENT_ALIASES,
    ALIGNMENT_COMMAND_PATTERN,
    ALIGNMENT_WORDS,
    CANONICAL_ALIGNMENTS,
    normalize_alignment,
    supported_label,
)
from engine.vocabulary.confirmation import APPROVAL_ALIASES, CANCEL_ALIASES

__all__ = [
    "ALIGNMENT_ALIASES",
    "ALIGNMENT_COMMAND_PATTERN",
    "ALIGNMENT_WORDS",
    "APPROVAL_ALIASES",
    "CANCEL_ALIASES",
    "CANONICAL_ALIGNMENTS",
    "normalize_alignment",
    "supported_label",
]
