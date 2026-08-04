"""Word operations, one module per user-visible action."""

from __future__ import annotations

from engine.app_actions.operations.contracts import OperationRegistry
from engine.app_actions.operations.word.base import WordSession
from engine.app_actions.operations.word.paragraph_format import (
    SetParagraphFormatOperation,
)
from engine.app_actions.operations.word.replace_selection import (
    ReplaceSelectionOperation,
)
from engine.app_actions.operations.word.save_document import SaveDocumentOperation
from engine.app_actions.operations.word.state import (
    WD_ALIGNMENTS,
    apply_text_format,
    normalize_alignment,
)
from engine.app_actions.operations.word.text_format import SetTextFormatOperation

WORD_OPERATIONS = OperationRegistry(
    "word",
    "Word",
    (
        ReplaceSelectionOperation(),
        SetTextFormatOperation(),
        SetParagraphFormatOperation(),
        SaveDocumentOperation(),
    ),
)

__all__ = [
    "WD_ALIGNMENTS",
    "WORD_OPERATIONS",
    "WordSession",
    "apply_text_format",
    "normalize_alignment",
]
