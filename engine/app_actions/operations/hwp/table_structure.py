"""Add, remove and merge cells in an existing 한글 table.

한글 exposes no row or column count, so the table's shape is derived the same
way a cell is addressed: by stepping and watching ``GetPos()[0]`` stop
changing.  Knowing the shape is what makes verification exact — inserting a
row must add exactly one row's worth of cells, not merely "more than before".

Confirmed against a real 한글 install:

* ``TableInsertLowerRow`` / ``TableInsertRightColumn`` add one line each.
* ``TableDeleteRow`` / ``TableDeleteColumn`` remove the line the caret is on.
* Merging needs a real block: ``TableCellBlockExtend`` then a move, then
  ``TableMergeCell``.  ``TableMergeCell`` alone reports failure.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation
from engine.app_actions.operations.hwp.table_cell import (
    cell_identity,
    cell_slots,
    enter_first_cell,
    first_table_control,
    step_to_cell,
)

MAX_STEPS = 200


# The table's saveblock text is bracketed by an empty slot at each end, so a
# 3x4 table reports 14 slots for 12 cells. Measured, not assumed.
SLOT_PADDING = 2


def cell_count(hwp) -> int:
    """Cells in the table the caret sits in."""
    return max(0, len(cell_slots(hwp)) - SLOT_PADDING)


def table_dimensions(hwp, control):
    """Derive (rows, columns) from one downward step.

    Columns cannot be counted by stepping right: ``TableRightCell`` wraps to
    the next row and only stops at the very last cell, so it walks the whole
    table.  ``TableLowerCell`` moves down exactly one row, and cell identity is
    row-major, so the identity it gains *is* the column count.
    """
    # Measuring moves the caret, and a preview must leave it where the user
    # put it: the caret is part of the fingerprint that decides whether an
    # approved edit may still run.
    origin = hwp.GetPos()
    try:
        enter_first_cell(hwp, control)
        total = cell_count(hwp)
        if total <= 0:
            raise AppActionBlocked("표의 칸을 읽지 못해 구조를 바꾸지 않았습니다.")
        before = cell_identity(hwp)
        hwp.HAction.Run("TableLowerCell")
        columns = cell_identity(hwp) - before
        if columns <= 0:
            # A single-row table: nothing below to step to.
            columns = total
        if total % columns:
            raise AppActionBlocked(
                "표의 행과 열을 확인하지 못해 구조를 바꾸지 않았습니다."
            )
        return total // columns, columns
    finally:
        try:
            hwp.HAction.Run("Cancel")
            hwp.SetPos(*origin)
        except Exception:
            pass


class TableStructureOperation(HwpOperation):
    """One structural edit, defined by its action and the cells it changes."""

    action = ""
    delta_axis = ""  # "columns" when a row changes, "rows" when a column does
    sign = 1

    def _address(self, params):
        row = int(params.get("row", 1) or 1)
        column = int(params.get("column", params.get("col", 1)) or 1)
        if row < 1 or column < 1:
            raise AppActionBlocked("표의 행·열 번호는 1부터 시작합니다.")
        return row, column

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = first_table_control(hwp)
        if control is None:
            raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
        row, column = self._address(params)
        rows, columns = table_dimensions(hwp, control)
        if row > rows or column > columns:
            raise AppActionBlocked(
                f"표는 {rows}행 {columns}열이라 요청한 칸이 없습니다."
            )
        enter_first_cell(hwp, control)
        total = cell_count(hwp)
        changed = columns if self.delta_axis == "columns" else rows
        expected = total + self.sign * changed
        if expected <= 0:
            raise AppActionBlocked("마지막 행 또는 열은 삭제하지 않습니다.")
        target = f"표 {row}행 {column}열"
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "rows": rows,
            "columns": columns,
            "cell_count": total,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "row": row,
                "column": column,
                "expected_cell_count": expected,
            },
            current_state={
                "rows": rows,
                "columns": columns,
                "cell_count": total,
                "document_digest": base["text_digest"],
            },
            estimated_changes=changed,
            destructive=self.sign < 0,
            reversible=True,
            verification_method="count_table_cells",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def apply(self, hwp):
        if not hwp.HAction.Run(self.action):
            raise AppActionBlocked(
                "한글이 현재 칸에서 이 표 편집을 허용하지 않았습니다."
            )

    def run(self, adapter, hwp, current):
        row = int(current.params["row"])
        column = int(current.params["column"])
        expected = int(current.params["expected_cell_count"])
        before = current.current_state["cell_count"]
        try:
            control = first_table_control(hwp)
            if control is None:
                raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
            enter_first_cell(hwp, control)
            step_to_cell(hwp, row, column)
            self.apply(hwp)
            # Changing the structure drops the cell block, and without it
            # saveblock no longer returns the whole table, so the count has to
            # be taken after re-entering.
            enter_first_cell(hwp, control)
            after = cell_count(hwp)
            if after != expected:
                raise AppActionVerificationError(
                    "한글 표 구조 변경 결과가 예상과 다릅니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 구조 변경 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"cell_count": before},
            {"cell_count": after},
            True,
        )


class InsertTableRowOperation(TableStructureOperation):
    name = "insert_table_row"
    action = "TableInsertLowerRow"
    delta_axis = "columns"
    sign = 1


class InsertTableColumnOperation(TableStructureOperation):
    name = "insert_table_column"
    action = "TableInsertRightColumn"
    delta_axis = "rows"
    sign = 1


class DeleteTableRowOperation(TableStructureOperation):
    name = "delete_table_row"
    action = "TableDeleteRow"
    delta_axis = "columns"
    sign = -1


class DeleteTableColumnOperation(TableStructureOperation):
    name = "delete_table_column"
    action = "TableDeleteColumn"
    delta_axis = "rows"
    sign = -1


class MergeTableCellsOperation(HwpOperation):
    """Merge the addressed cell with the one to its right.

    Merging needs a real selection block: entering cell-select mode is not
    enough, so the block is extended and moved before merging. 한글 reports
    failure from TableMergeCell when the block covers a single cell.
    """

    name = "merge_table_cells"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = first_table_control(hwp)
        if control is None:
            raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
        row = int(params.get("row", 1) or 1)
        column = int(params.get("column", params.get("col", 1)) or 1)
        rows, columns = table_dimensions(hwp, control)
        if row > rows or column >= columns:
            raise AppActionBlocked(
                f"표는 {rows}행 {columns}열이라 오른쪽으로 병합할 칸이 없습니다."
            )
        enter_first_cell(hwp, control)
        total = cell_count(hwp)
        target = f"표 {row}행 {column}~{column + 1}열"
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "rows": rows,
            "columns": columns,
            "cell_count": total,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "row": row,
                "column": column,
                "expected_cell_count": total - 1,
            },
            current_state={
                "rows": rows,
                "columns": columns,
                "cell_count": total,
                "document_digest": base["text_digest"],
            },
            estimated_changes=2,
            destructive=True,
            reversible=True,
            verification_method="count_table_cells",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        row = int(current.params["row"])
        column = int(current.params["column"])
        expected = int(current.params["expected_cell_count"])
        before = current.current_state["cell_count"]
        try:
            control = first_table_control(hwp)
            if control is None:
                raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
            enter_first_cell(hwp, control)
            step_to_cell(hwp, row, column)
            hwp.HAction.Run("TableCellBlockExtend")
            hwp.HAction.Run("TableRightCell")
            if not hwp.HAction.Run("TableMergeCell"):
                raise AppActionBlocked(
                    "한글이 선택한 칸들의 병합을 허용하지 않았습니다."
                )
            enter_first_cell(hwp, control)
            after = cell_count(hwp)
            if after != expected:
                raise AppActionVerificationError(
                    "한글 표 칸 병합 결과가 예상과 다릅니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 칸 병합 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"cell_count": before},
            {"cell_count": after},
            True,
        )


__all__ = [
    "DeleteTableColumnOperation",
    "DeleteTableRowOperation",
    "InsertTableColumnOperation",
    "InsertTableRowOperation",
    "MergeTableCellsOperation",
    "TableStructureOperation",
    "cell_count",
    "table_dimensions",
]
