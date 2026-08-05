"""Excel operations, one module per user-visible action."""

from __future__ import annotations

from engine.app_actions.operations.contracts import OperationRegistry
from engine.app_actions.operations.excel.conditional_format import (
    COLOR_VALUES,
    CONDITION_OPERATORS,
    ApplyConditionalFormatOperation,
    FormatMatchingValuesOperation,
    condition_true,
    normalize_color,
    normalize_operator,
    normalize_threshold,
)
from engine.app_actions.operations.excel.filter_range import FilterRangeOperation
from engine.app_actions.operations.excel.find_replace import FindReplaceOperation
from engine.app_actions.operations.excel.range_format import FormatRangeOperation
from engine.app_actions.operations.excel.ribbon import (
    EXCEL_COMMANDS,
    RunExcelCommandOperation,
)
from engine.app_actions.operations.excel.sort_range import (
    SortRangeOperation,
    table_values,
)
from engine.app_actions.operations.excel.structure_insert import (
    InsertColumnsOperation,
    InsertRowsOperation,
)
from engine.app_actions.operations.excel.sum_column import SumColumnToCellOperation
from engine.app_actions.operations.excel.write_cell import WriteCellOperation

EXCEL_OPERATIONS = OperationRegistry(
    "excel",
    "Excel",
    (
        WriteCellOperation(),
        SumColumnToCellOperation(),
        FormatRangeOperation(),
        FilterRangeOperation(),
        FindReplaceOperation(),
        SortRangeOperation(),
        InsertRowsOperation(),
        InsertColumnsOperation(),
        ApplyConditionalFormatOperation(),
        FormatMatchingValuesOperation(),
        RunExcelCommandOperation(),
    ),
)

# Derived, not hand-maintained: a reversible operation is one that brought its
# own restore path, so adding one cannot forget to register it here.
EXCEL_UNDO_OPERATIONS = frozenset(
    name
    for name in EXCEL_OPERATIONS.names
    if getattr(EXCEL_OPERATIONS.require(name), "undo", None) is not None
)

__all__ = [
    "EXCEL_COMMANDS",
    "COLOR_VALUES",
    "CONDITION_OPERATORS",
    "EXCEL_OPERATIONS",
    "EXCEL_UNDO_OPERATIONS",
    "condition_true",
    "normalize_color",
    "normalize_operator",
    "normalize_threshold",
    "table_values",
]
