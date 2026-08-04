"""PowerPoint operations, one module per user-visible action."""

from __future__ import annotations

from engine.app_actions.operations.contracts import OperationRegistry
from engine.app_actions.operations.powerpoint.alignment import (
    SetTextAlignmentOperation,
)
from engine.app_actions.operations.powerpoint.base import (
    PowerPointSession,
    PowerPointTarget,
)
from engine.app_actions.operations.powerpoint.match_style import (
    MatchPreviousStyleOperation,
)
from engine.app_actions.operations.powerpoint.move_shape import MoveShapeOperation
from engine.app_actions.operations.powerpoint.replace_shape_text import (
    ReplaceShapeTextOperation,
)
from engine.app_actions.operations.powerpoint.resize_shape import ResizeShapeOperation
from engine.app_actions.operations.powerpoint.state import (
    PPT_ALIGNMENTS,
    normalize_alignment,
    uniform_style_value,
)
from engine.app_actions.operations.powerpoint.text_format import (
    SetTextFormatOperation,
)

POWERPOINT_OPERATIONS = OperationRegistry(
    "powerpoint",
    "PowerPoint",
    (
        ReplaceShapeTextOperation(),
        SetTextFormatOperation(),
        SetTextAlignmentOperation(),
        MoveShapeOperation(),
        ResizeShapeOperation(),
        MatchPreviousStyleOperation(),
    ),
)

__all__ = [
    "POWERPOINT_OPERATIONS",
    "PPT_ALIGNMENTS",
    "PowerPointSession",
    "PowerPointTarget",
    "normalize_alignment",
    "uniform_style_value",
]
