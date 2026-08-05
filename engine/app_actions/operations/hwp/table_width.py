"""Resize a 한글 table column.

한글 will not take a width through the parameter set.  ``ShapeTableCell.Width``
can be read, and writing it reports success, but the column does not move — for
every ``ApplyTo`` value.  The only mechanism that works is the incremental
``TableResizeCellRight`` / ``TableResizeCellLeft`` pair, each of which moves the
column by one millimetre.

So a target width is reached by stepping toward it, which makes three things
load-bearing and all three were measured against a real 한글 install:

* the step is 283 HWPUNIT in both directions, so the number of steps is known
  before any of them is taken and the loop is bounded rather than a search;
* the final width is verified against the request, so a step that silently
  refuses cannot pass as success;
* 한글 collapses the whole run into one undo entry — five steps forward came
  back with a single Undo — so restoring does not need to count backwards.
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
    enter_first_cell,
    first_table_control,
    step_to_cell,
)
from engine.app_actions.operations.hwp.table_structure import table_dimensions
from engine.vocabulary.table_size import (
    column_width_label,
    normalize_column_width_mm,
)

GROW_ACTION = "TableResizeCellRight"
SHRINK_ACTION = "TableResizeCellLeft"

# One step is one millimetre. Measured in both directions, not assumed.
STEP_UNITS = 283
MAX_RESIZE_STEPS = 250


def column_width(hwp) -> int:
    """The addressed cell's width in HWPUNIT."""
    shape = hwp.HParameterSet.HShapeObject
    hwp.HAction.GetDefault("TablePropertyDialog", shape.HSet)
    return int(shape.ShapeTableCell.Width)


class SetTableColumnWidthOperation(HwpOperation):
    name = "set_table_column_width"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = first_table_control(hwp)
        if control is None:
            raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
        row = int(params.get("row", 1) or 1)
        column = int(params.get("column", params.get("col", 1)) or 1)
        try:
            millimetres = normalize_column_width_mm(
                params.get("width_mm")
                if params.get("width_mm") is not None
                else params.get("width")
            )
        except ValueError as error:
            raise AppActionBlocked(str(error)) from error
        rows, columns = table_dimensions(hwp, control)
        if row > rows or column > columns:
            raise AppActionBlocked(
                f"표는 {rows}행 {columns}열이라 요청한 칸이 없습니다."
            )
        enter_first_cell(hwp, control)
        step_to_cell(hwp, row, column)
        current = column_width(hwp)
        wanted = int(hwp.MiliToHwpUnit(millimetres))
        steps = round((wanted - current) / STEP_UNITS)
        if abs(steps) > MAX_RESIZE_STEPS:
            raise AppActionBlocked(
                "요청한 너비까지의 변화가 너무 커서 조절하지 않았습니다."
            )
        target = f"표 {column}열"
        label = column_width_label(millimetres)
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "width": current,
            "wanted": wanted,
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
                "width_mm": millimetres,
                "wanted_units": wanted,
                "steps": steps,
                "column_width_label": label,
            },
            current_state={
                "width": current,
                "rows": rows,
                "columns": columns,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if steps == 0 else rows,
            destructive=False,
            reversible=True,
            verification_method="read_table_column_width",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=steps == 0,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = {"width": current.current_state["width"]}
        if current.noop:
            return adapter._result(current, before, before, False)
        steps = int(current.params["steps"])
        wanted = int(current.params["wanted_units"])
        action = GROW_ACTION if steps > 0 else SHRINK_ACTION
        try:
            control = first_table_control(hwp)
            if control is None:
                raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
            enter_first_cell(hwp, control)
            step_to_cell(
                hwp, int(current.params["row"]), int(current.params["column"])
            )
            for _ in range(abs(steps)):
                if not hwp.HAction.Run(action):
                    raise AppActionBlocked(
                        "한글이 현재 칸에서 열 너비 조절을 허용하지 않았습니다."
                    )
            enter_first_cell(hwp, control)
            step_to_cell(
                hwp, int(current.params["row"]), int(current.params["column"])
            )
            after = column_width(hwp)
            # One step of tolerance: the column can only land on multiples of
            # the step from where it started.
            if abs(after - wanted) > STEP_UNITS:
                raise AppActionVerificationError(
                    "한글 열 너비 조절 결과가 요청과 다릅니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 열 너비 조절 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            before,
            {"width": after, "width_mm": current.params["width_mm"]},
            True,
        )


__all__ = [
    "GROW_ACTION",
    "MAX_RESIZE_STEPS",
    "SHRINK_ACTION",
    "STEP_UNITS",
    "SetTableColumnWidthOperation",
    "column_width",
]
