"""Write text into one cell of a 한글 table.

한글 has no "cell at (row, column)" accessor and no readable row/column count,
so everything here is derived from two behaviours confirmed against a real
한글 install by `verification/hwp_table_probe.py`:

* ``GetPos()[0]`` changes per cell and is therefore the cell's identity.  The
  first cell reports 2, and it increases in row-major order.  Stepping past the
  last cell stops changing it, which is what bounds an out-of-range address.
* ``GetTextFile("UNICODE", "saveblock")`` inside a table returns *the whole
  table*, one cell per ``

`` separated slot, not the selected cell.  Slot
  ``GetPos()[0] - 1`` is the addressed cell.

So a cell is reached by stepping, identified by its list index, and read by
indexing the table's text.  Never assume any of this from the fake: the fake
mirrors these findings, it does not establish them.
"""

from __future__ import annotations

from contextlib import contextmanager

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import (
    MAX_INSERT_TEXT_CHARS,
    HwpOperation,
)
from engine.app_actions.operations.hwp.insert_table import (
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    TABLE_CONTROL_ID,
)

# 한글 separates a table's cells with CRLF in its saveblock text.
CELL_SEPARATOR = "\r\n"



def _address(params, keys, label, maximum):
    value = None
    for key in keys:
        if params.get(key) is not None:
            value = params.get(key)
            break
    if value is None:
        raise AppActionBlocked(f"표에서 바꿀 칸의 {label} 번호를 지정해주세요.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise AppActionBlocked(f"칸의 {label} 번호는 숫자여야 합니다.") from error
    if not 1 <= number <= maximum:
        raise AppActionBlocked(f"칸의 {label} 번호는 1부터 {maximum} 사이여야 합니다.")
    return number


def first_table_control(hwp):
    control = getattr(hwp, "HeadCtrl", None)
    seen = 0
    while control is not None and seen < 10_000:
        seen += 1
        if str(getattr(control, "CtrlID", "")) == TABLE_CONTROL_ID:
            return control
        control = getattr(control, "Next", None)
    return None


def enter_first_cell(hwp, control):
    """Place the caret inside the table's top-left cell."""
    hwp.SetPosBySet(control.GetAnchorPos(0))
    hwp.FindCtrl()
    hwp.HAction.Run("ShapeObjTableSelCell")


def step_to_cell(hwp, row, column):
    """Step from the top-left cell to (row, column), 1-based.

    ``TableRightCell`` wraps to the next row at the end of a row in 한글, so
    columns are stepped before rows and each step is checked, otherwise a
    request past the last column would silently land somewhere else.
    """
    for _ in range(column - 1):
        if not _stepped(hwp, "TableRightCell"):
            raise AppActionBlocked("표에 요청한 열이 없어 칸을 바꾸지 않았습니다.")
    for _ in range(row - 1):
        if not _stepped(hwp, "TableLowerCell"):
            raise AppActionBlocked("표에 요청한 행이 없어 칸을 바꾸지 않았습니다.")


def _stepped(hwp, action) -> bool:
    before = hwp.GetPos()
    hwp.HAction.Run(action)
    return tuple(hwp.GetPos()) != tuple(before)


def cell_slots(hwp) -> list:
    """Every cell of the table the caret sits in, in row-major order."""
    whole = str(hwp.GetTextFile("UNICODE", "saveblock") or "")
    return whole.split(CELL_SEPARATOR)


def cell_identity(hwp) -> int:
    """The caret's cell, as 한글's own list index."""
    return int(hwp.GetPos()[0])


def read_addressed_cell(hwp):
    """Return (cell identity, that cell's text) for the caret's cell."""
    identity = cell_identity(hwp)
    slots = cell_slots(hwp)
    index = identity - 1
    if not 0 <= index < len(slots):
        raise AppActionBlocked("표에서 지정한 칸을 읽지 못했습니다.")
    return identity, slots[index]


@contextmanager
def visiting_cell(hwp, control, row, column):
    """Step to a cell and put the caret back where the user left it.

    한글 cannot read a cell without moving the caret there, but a preview must
    leave the document exactly as it found it — and the caret is state the user
    can see. Restoring it also keeps the re-prepare fingerprint comparison
    honest, since the caret position is part of that fingerprint.
    """
    origin = hwp.GetPos()
    try:
        enter_first_cell(hwp, control)
        step_to_cell(hwp, row, column)
        yield
    finally:
        try:
            hwp.HAction.Run("Cancel")
            hwp.SetPos(*origin)
        except Exception:
            pass


class SetTableCellOperation(HwpOperation):
    name = "set_table_cell"

    def prepare(self, adapter, hwp, params):
        _, base, _, _ = adapter._context(hwp)
        row = _address(params, ("row",), "행", MAX_TABLE_ROWS)
        column = _address(params, ("column", "col"), "열", MAX_TABLE_COLUMNS)
        text = str(params.get("text") if params.get("text") is not None else "")
        if len(text) > MAX_INSERT_TEXT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"칸에 넣을 텍스트는 최대 {MAX_INSERT_TEXT_CHARS:,}자이며 NUL 문자를 포함할 수 없습니다."
            )
        control = first_table_control(hwp)
        if control is None:
            raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
        with visiting_cell(hwp, control, row, column):
            identity, current_text = read_addressed_cell(hwp)
        noop = current_text == text
        target = f"표 {row}행 {column}열"
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "row": row,
            "column": column,
            "cell_digest": adapter._digest_text(current_text),
            "requested_digest": adapter._digest_text(text),
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
                "text": text,
                "original_text": current_text,
                "cell_identity": identity,
            },
            current_state={
                "row": row,
                "column": column,
                "cell_text_length": len(current_text),
                "cell_digest": adapter._digest_text(current_text),
                "cell_preview": current_text[:120],
                "document_digest": base["text_digest"],
                "format": {"cell_text": current_text},
            },
            estimated_changes=0 if noop else 1,
            destructive=bool(current_text) and not noop,
            reversible=True,
            verification_method="read_table_cell_text",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = {"cell_text": current.params["original_text"]}
        if current.noop:
            return adapter._result(current, before, before, False)
        row = int(current.params["row"])
        column = int(current.params["column"])
        text = current.params["text"]
        try:
            control = first_table_control(hwp)
            if control is None:
                raise AppActionBlocked("현재 한글 문서에 표가 없습니다.")
            expected_identity = int(current.params["cell_identity"])
            with visiting_cell(hwp, control, row, column):
                if cell_identity(hwp) != expected_identity:
                    raise AppActionVerificationError(
                        "표에서 확인한 칸과 다른 칸에 커서가 있어 입력하지 않았습니다."
                    )
                before_slots = cell_slots(hwp)
                action = hwp.CreateAction("InsertText")
                parameter_set = action.CreateSet()
                parameter_set.SetItem("Text", text)
                action.Execute(parameter_set)
            with visiting_cell(hwp, control, row, column):
                identity, actual = read_addressed_cell(hwp)
                after_slots = cell_slots(hwp)
            if identity != expected_identity or actual != text:
                raise AppActionVerificationError("한글 표 칸의 입력 결과가 요청과 다릅니다.")
            changed = [
                index
                for index, (was, now) in enumerate(zip(before_slots, after_slots))
                if was != now
            ]
            if changed != [expected_identity - 1]:
                raise AppActionVerificationError(
                    "한글 표에서 요청한 칸 외의 내용도 바뀌어 결과를 승인하지 않았습니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 칸 입력 또는 검증에 실패했습니다."
            ) from error
        _, after_base, _, _ = adapter._context(hwp)
        return adapter._result(
            current,
            before,
            {
                "cell_text": actual,
                "row": row,
                "column": column,
                "document_digest": after_base["text_digest"],
            },
            True,
        )


__all__ = [
    "SetTableCellOperation",
    "enter_first_cell",
    "first_table_control",
    "cell_identity",
    "cell_slots",
    "read_addressed_cell",
    "step_to_cell",
    "visiting_cell",
]
