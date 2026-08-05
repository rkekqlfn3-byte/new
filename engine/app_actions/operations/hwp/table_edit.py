"""Delete a 한글 table, split a cell, and set cell borders.

Each mechanism was found by asking 한글 rather than by assuming:

* ``TableDeleteTable`` does nothing.  A table is removed by selecting its
  control and deleting it, which reports success and drops the control count.
* ``TableSplitCell`` takes ``Rows``/``Cols`` through its parameter set and
  splits the addressed cell, so the resulting cell count is predictable.
* ``CellBorderFill`` carries the border type, width and colour per edge, and
  writing the width round-trips.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation
from engine.app_actions.operations.hwp.insert_table import table_control_count
from engine.app_actions.operations.hwp.state import COLOR_RGB, normalize_color_name
from engine.app_actions.operations.hwp.table_cell import (
    enter_first_cell,
    first_table_control,
    step_to_cell,
)
from engine.app_actions.operations.hwp.table_structure import (
    cell_count,
    table_dimensions,
)

MAX_SPLIT = 10

# HWP border width is an enum, not a measurement, so it is exposed by name.
BORDER_WIDTHS = {"얇게": 1, "보통": 3, "굵게": 6}
BORDER_EDGES = ("Top", "Bottom", "Left", "Right")
SOLID_BORDER = 1


def _table_or_block(hwp):
    control = first_table_control(hwp)
    if control is None:
        raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
    return control


class DeleteTableOperation(HwpOperation):
    name = "delete_table"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = _table_or_block(hwp)
        tables = table_control_count(hwp)
        origin = hwp.GetPos()
        try:
            rows, columns = table_dimensions(hwp, control)
        finally:
            try:
                hwp.HAction.Run("Cancel")
                hwp.SetPos(*origin)
            except Exception:
                pass
        snapshot = {
            **base,
            "operation": self.name,
            "target": "표",
            "table_count": tables,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{rows}행 {columns}열 표",
            params={"expected_table_count": tables - 1},
            current_state={
                "table_count": tables,
                "rows": rows,
                "columns": columns,
                "document_digest": base["text_digest"],
            },
            estimated_changes=rows * columns,
            destructive=True,
            reversible=True,
            verification_method="count_table_controls",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["table_count"]
        expected = int(current.params["expected_table_count"])
        try:
            control = _table_or_block(hwp)
            # TableDeleteTable does nothing; selecting the control and deleting
            # it is what 한글 actually accepts.
            hwp.SetPosBySet(control.GetAnchorPos(0))
            hwp.FindCtrl()
            if not hwp.HAction.Run("Delete"):
                raise AppActionBlocked("한글이 표 삭제를 허용하지 않았습니다.")
            after = table_control_count(hwp)
            if after != expected:
                raise AppActionVerificationError("한글 표 삭제 결과가 예상과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 삭제 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current, {"table_count": before}, {"table_count": after}, True
        )


class SplitTableCellOperation(HwpOperation):
    name = "split_table_cell"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = _table_or_block(hwp)
        row = int(params.get("row", 1) or 1)
        column = int(params.get("column", params.get("col", 1)) or 1)
        into_rows = int(params.get("rows", 1) or 1)
        into_columns = int(params.get("columns", params.get("cols", 2)) or 2)
        if not 1 <= into_rows <= MAX_SPLIT or not 1 <= into_columns <= MAX_SPLIT:
            raise AppActionBlocked(f"칸 분할은 1부터 {MAX_SPLIT}까지 지원합니다.")
        if into_rows * into_columns < 2:
            raise AppActionBlocked("칸을 둘 이상으로 나눠야 합니다.")
        rows, columns = table_dimensions(hwp, control)
        if row > rows or column > columns:
            raise AppActionBlocked(
                f"표는 {rows}행 {columns}열이라 요청한 칸이 없습니다."
            )
        enter_first_cell(hwp, control)
        total = cell_count(hwp)
        target = f"표 {row}행 {column}열"
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "cell_count": total,
            "into": [into_rows, into_columns],
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
                "rows": into_rows,
                "columns": into_columns,
                "expected_cell_count": total + into_rows * into_columns - 1,
            },
            current_state={
                "cell_count": total,
                "document_digest": base["text_digest"],
            },
            estimated_changes=into_rows * into_columns,
            destructive=False,
            reversible=True,
            verification_method="count_table_cells",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["cell_count"]
        expected = int(current.params["expected_cell_count"])
        try:
            control = _table_or_block(hwp)
            enter_first_cell(hwp, control)
            step_to_cell(
                hwp, int(current.params["row"]), int(current.params["column"])
            )
            parameter_set = hwp.HParameterSet.HTableSplitCell
            hwp.HAction.GetDefault("TableSplitCell", parameter_set.HSet)
            parameter_set.Rows = int(current.params["rows"])
            parameter_set.Cols = int(current.params["columns"])
            parameter_set.Merge = 0
            hwp.HAction.Execute("TableSplitCell", parameter_set.HSet)
            enter_first_cell(hwp, control)
            after = cell_count(hwp)
            if after != expected:
                raise AppActionVerificationError("한글 칸 분할 결과가 예상과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 칸 분할 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current, {"cell_count": before}, {"cell_count": after}, True
        )


def border_state(hwp) -> dict:
    fill = hwp.HParameterSet.HCellBorderFill
    hwp.HAction.GetDefault("CellBorderFill", fill.HSet)
    return {
        edge.casefold(): int(getattr(fill, f"BorderWidth{edge}"))
        for edge in BORDER_EDGES
    }


class SetTableBorderOperation(HwpOperation):
    name = "set_table_border"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        control = _table_or_block(hwp)
        row = int(params.get("row", 1) or 1)
        column = int(params.get("column", params.get("col", 1)) or 1)
        thickness = str(params.get("thickness") or params.get("width") or "").strip()
        if thickness not in BORDER_WIDTHS:
            raise AppActionBlocked("표 테두리는 얇게·보통·굵게를 지원합니다.")
        colour = params.get("color")
        colour_name = normalize_color_name(colour) if colour is not None else None
        rows, columns = table_dimensions(hwp, control)
        if row > rows or column > columns:
            raise AppActionBlocked(
                f"표는 {rows}행 {columns}열이라 요청한 칸이 없습니다."
            )
        enter_first_cell(hwp, control)
        step_to_cell(hwp, row, column)
        current = border_state(hwp)
        wanted = BORDER_WIDTHS[thickness]
        noop = all(value == wanted for value in current.values()) and colour is None
        target = f"표 {row}행 {column}열"
        label = f"테두리 {thickness}" + (f" · {colour_name}" if colour_name else "")
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "border": current,
            "wanted": wanted,
            "color": colour_name,
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
                "thickness": thickness,
                "width": wanted,
                "color": colour_name,
                "border_label": label,
            },
            current_state={
                "border": current,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if noop else 1,
            destructive=False,
            reversible=True,
            verification_method="read_cell_border_width",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["border"]
        if current.noop:
            return adapter._result(current, before, before, False)
        wanted = int(current.params["width"])
        colour_name = current.params.get("color")
        try:
            control = _table_or_block(hwp)
            enter_first_cell(hwp, control)
            step_to_cell(
                hwp, int(current.params["row"]), int(current.params["column"])
            )
            fill = hwp.HParameterSet.HCellBorderFill
            hwp.HAction.GetDefault("CellBorderFill", fill.HSet)
            for edge in BORDER_EDGES:
                setattr(fill, f"BorderType{edge}", SOLID_BORDER)
                setattr(fill, f"BorderWidth{edge}", wanted)
                if colour_name:
                    # 한글 spells one of these attributes without the 'o'.
                    attribute = (
                        "BorderCorlorLeft" if edge == "Left" else f"BorderColor{edge}"
                    )
                    if hasattr(fill, attribute):
                        setattr(fill, attribute, hwp.RGBColor(*COLOR_RGB[colour_name]))
            hwp.HAction.Execute("CellBorderFill", fill.HSet)
            enter_first_cell(hwp, control)
            step_to_cell(
                hwp, int(current.params["row"]), int(current.params["column"])
            )
            after = border_state(hwp)
            if any(value != wanted for value in after.values()):
                raise AppActionVerificationError("한글 표 테두리 적용 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 테두리 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = [
    "BORDER_EDGES",
    "BORDER_WIDTHS",
    "DeleteTableOperation",
    "SetTableBorderOperation",
    "SplitTableCellOperation",
    "border_state",
]
