"""Write one value or formula into one Excel cell."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionContextChanged,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import ExcelOperation
from engine.app_actions.value_normalizer import (
    normalize_cell_address,
    normalize_excel_input,
)


def undo_cell_write(adapter, application, prepared):
    """Restore a single written cell.

    Shared with ``sum_column_to_cell``: both write one cell, so both restore
    the same way.
    """
    sheet = adapter._ensure_undo_context(application, prepared)
    cell = sheet.Range(prepared.target)
    current = adapter._cell_snapshot(cell)
    desired = {
        "kind": prepared.params["value_type"],
        "value": prepared.params["value"],
    }
    if not adapter._matches_desired(current, desired):
        raise AppActionContextChanged(
            "직전 편집 뒤 대상 셀 값이 달라져 안전하게 복원하지 않았습니다."
        )
    original = {
        "formula": prepared.current_state.get("formula"),
        "value": prepared.current_state.get("value"),
    }
    adapter._restore_cell_value(cell, original)
    restored = adapter._cell_snapshot(cell)
    expected = {
        "kind": "formula" if original["formula"] is not None else "value",
        "value": (
            original["formula"]
            if original["formula"] is not None
            else original["value"]
        ),
    }
    if not adapter._matches_desired(restored, expected):
        raise AppActionVerificationError(
            "Excel 셀의 원래 값이 복원되었는지 확인하지 못했습니다."
        )
    return restored


class WriteCellOperation(ExcelOperation):
    name = "write_cell"

    def prepare(self, adapter, application, params):
        cell_address = normalize_cell_address(params.get("cell"))
        desired = normalize_excel_input(params.get("value"), params.get("value_type"))
        _, _, _, snapshot = adapter._active_context(application, cell_address)
        is_empty = adapter._is_empty(snapshot)
        noop = adapter._matches_desired(snapshot, desired)
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=snapshot["document_id"],
            workbook_name=snapshot["workbook_name"],
            sheet=snapshot["sheet"],
            target=snapshot["target"],
            params={
                "cell": snapshot["target"],
                "value": desired["value"],
                "value_type": desired["kind"],
            },
            current_state={
                "formula": snapshot["current_formula"],
                "value": snapshot["current_value"],
                "empty": is_empty,
            },
            estimated_changes=0 if noop else 1,
            destructive=not is_empty and not noop,
            reversible=True,
            verification_method=(
                "read_formula" if desired["kind"] == "formula" else "read_value2"
            ),
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": snapshot["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        _, _, cell, snapshot = adapter._active_context(application, prepared.target)
        if adapter._state_fingerprint(snapshot) != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인 이후 Excel 통합문서·시트 또는 셀 상태가 바뀌어 실행하지 않았습니다."
            )
        desired = {
            "kind": prepared.params["value_type"],
            "value": prepared.params["value"],
        }
        if prepared.noop:
            return adapter._result(prepared, snapshot, snapshot, changed=False)
        return adapter._write_and_verify(cell, snapshot, desired, prepared)

    undo = staticmethod(undo_cell_write)


__all__ = ["WriteCellOperation", "undo_cell_write"]
