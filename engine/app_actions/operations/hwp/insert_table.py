"""Insert a table at the 한글 cursor.

Unlike every other 한글 operation so far, this creates document structure
rather than changing existing text, so it is verified by counting table
controls before and after rather than by comparing document text.

The COM call shape here has no precedent in this repository — nothing else
creates a 한글 table — so `verification/hwp_table_probe.py` exists to confirm
it against a real 한글 install.  The unit tests prove the contract and the
safety checks; only the probe proves the automation call itself.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation

MAX_TABLE_ROWS = 100
MAX_TABLE_COLUMNS = 30

TABLE_CONTROL_ID = "tbl"


def table_control_count(hwp) -> int:
    """Count table controls by walking the document's control chain.

    한글 exposes every anchored object through ``HeadCtrl``/``Next``; tables
    report ``CtrlID == "tbl"``.  Counting is the only structural fact needed to
    verify an insertion, and it never reads document content.
    """
    count = 0
    control = getattr(hwp, "HeadCtrl", None)
    seen = 0
    while control is not None and seen < 10_000:
        seen += 1
        if str(getattr(control, "CtrlID", "")) == TABLE_CONTROL_ID:
            count += 1
        control = getattr(control, "Next", None)
    return count


def _dimension(params, keys, label, maximum):
    value = None
    for key in keys:
        if params.get(key) is not None:
            value = params.get(key)
            break
    if value is None:
        raise AppActionBlocked(f"표의 {label} 수를 지정해주세요.")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise AppActionBlocked(f"표의 {label} 수는 숫자여야 합니다.") from error
    if not 1 <= number <= maximum:
        raise AppActionBlocked(f"표의 {label} 수는 1부터 {maximum} 사이여야 합니다.")
    return number


class InsertTableOperation(HwpOperation):
    name = "insert_table"

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        if selection["has_selection"]:
            raise AppActionBlocked(
                "선택 영역이 있으면 표를 넣지 않습니다. 커서만 둔 뒤 다시 요청해주세요."
            )
        rows = _dimension(params, ("rows", "row_count"), "행", MAX_TABLE_ROWS)
        columns = _dimension(
            params, ("columns", "cols", "column_count"), "열", MAX_TABLE_COLUMNS
        )
        existing = table_control_count(hwp)
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._target(base, selection),
            "rows": rows,
            "columns": columns,
            "table_count": existing,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{rows}행 {columns}열 표",
            params={"rows": rows, "columns": columns},
            current_state={
                "table_count": existing,
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
                "position": base["position"],
            },
            estimated_changes=rows * columns,
            destructive=False,
            reversible=True,
            verification_method="count_table_controls",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        rows = int(current.params["rows"])
        columns = int(current.params["columns"])
        before = current.current_state["table_count"]
        try:
            creation = hwp.HParameterSet.HTableCreation
            hwp.HAction.GetDefault("TableCreate", creation.HSet)
            creation.Rows = rows
            creation.Cols = columns
            # 0 selects 한글's own sizing for both axes, which keeps the table
            # inside the page margins without this code computing widths.
            creation.WidthType = 0
            creation.HeightType = 0
            hwp.HAction.Execute("TableCreate", creation.HSet)
            after = table_control_count(hwp)
            if after != before + 1:
                raise AppActionVerificationError(
                    "한글에 표가 하나 추가되었는지 확인하지 못했습니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 표 삽입 또는 검증에 실패했습니다."
            ) from error
        _, after_base, _, _ = adapter._context(hwp)
        return adapter._result(
            current,
            {"table_count": before},
            {
                "table_count": after,
                "rows": rows,
                "columns": columns,
                "document_digest": after_base["text_digest"],
            },
            True,
        )


__all__ = [
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "InsertTableOperation",
    "table_control_count",
]
