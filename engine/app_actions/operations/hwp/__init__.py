"""Hanword operations, one module per user-visible action."""

from __future__ import annotations

from engine.app_actions.operations.contracts import OperationRegistry
from engine.app_actions.operations.hwp.delete_text import DeleteTextOperation
from engine.app_actions.operations.hwp.find_replace import FindReplaceOperation
from engine.app_actions.operations.hwp.insert_table import InsertTableOperation
from engine.app_actions.operations.hwp.insert_text import InsertTextOperation
from engine.app_actions.operations.hwp.line_spacing import SetLineSpacingOperation
from engine.app_actions.operations.hwp.list_format import SetListFormatOperation
from engine.app_actions.operations.hwp.page_break import InsertPageBreakOperation
from engine.app_actions.operations.hwp.paragraph_format import (
    SetParagraphFormatOperation,
)
from engine.app_actions.operations.hwp.save_as import SaveAsOperation
from engine.app_actions.operations.hwp.state import (
    COLOR_RGB,
    PARAGRAPH_ALIGNMENTS,
    char_state,
    paragraph_state,
)
from engine.app_actions.operations.hwp.table_cell import SetTableCellOperation
from engine.app_actions.operations.hwp.table_structure import (
    DeleteTableColumnOperation,
    DeleteTableRowOperation,
    InsertTableColumnOperation,
    InsertTableRowOperation,
    MergeTableCellsOperation,
)
from engine.app_actions.operations.hwp.table_width import SetTableColumnWidthOperation
from engine.app_actions.operations.hwp.text_format import SetTextFormatOperation

HWP_OPERATIONS = OperationRegistry(
    "hwp",
    "한글",
    (
        InsertTextOperation(),
        DeleteTextOperation(),
        InsertTableOperation(),
        SetTableCellOperation(),
        InsertTableRowOperation(),
        InsertTableColumnOperation(),
        DeleteTableRowOperation(),
        DeleteTableColumnOperation(),
        MergeTableCellsOperation(),
        SetTableColumnWidthOperation(),
        SetTextFormatOperation(),
        SetParagraphFormatOperation(),
        SetLineSpacingOperation(),
        SetListFormatOperation(),
        InsertPageBreakOperation(),
        FindReplaceOperation(),
        SaveAsOperation(),
    ),
)

__all__ = [
    "COLOR_RGB",
    "HWP_OPERATIONS",
    "PARAGRAPH_ALIGNMENTS",
    "char_state",
    "paragraph_state",
]
