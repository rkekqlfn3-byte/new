"""Insert blank rows or columns and prove the existing data shifted intact."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import MAX_SOURCE_CELLS, ExcelOperation
from engine.app_actions.value_normalizer import (
    excel_column_letters,
    normalize_range_address,
)


def undo_structure_insert(adapter, application, prepared):
    sheet = adapter._ensure_undo_context(application, prepared)
    axis = prepared.params["axis"]
    index = int(prepared.params["index"])
    count = int(prepared.params["count"])
    first_column = int(prepared.params["used_first_column"])
    first_row = int(prepared.params["used_first_row"])
    last_column = int(prepared.params["used_last_column"])
    last_row = int(prepared.params["used_last_row"])
    if axis == "row":
        shifted = sheet.Range(
            f"{excel_column_letters(first_column)}{index + count}:"
            f"{excel_column_letters(last_column)}{last_row + count}"
        )
        blank = sheet.Range(
            f"{excel_column_letters(first_column)}{index}:"
            f"{excel_column_letters(last_column)}{index + count - 1}"
        )
        inserted = sheet.Rows(f"{index}:{index + count - 1}")
        original_address = (
            f"{excel_column_letters(first_column)}{index}:"
            f"{excel_column_letters(last_column)}{last_row}"
        )
    else:
        first = excel_column_letters(index)
        last = excel_column_letters(index + count - 1)
        shifted = sheet.Range(
            f"{excel_column_letters(index + count)}{first_row}:"
            f"{excel_column_letters(last_column + count)}{last_row}"
        )
        blank = sheet.Range(f"{first}{first_row}:{last}{last_row}")
        inserted = sheet.Columns(f"{first}:{last}")
        original_address = (
            f"{excel_column_letters(index)}{first_row}:"
            f"{excel_column_letters(last_column)}{last_row}"
        )
    if (
        adapter._range_digest(shifted) != prepared.params["shifted_digest"]
        or not adapter._range_is_blank(blank)
    ):
        raise AppActionContextChanged(
            "삽입된 행·열 또는 이동된 데이터가 달라져 안전하게 복원하지 않았습니다."
        )
    inserted.Delete()
    restored_digest = adapter._range_digest(sheet.Range(original_address))
    if restored_digest != prepared.params["shifted_digest"]:
        raise AppActionVerificationError(
            "Excel 행·열 삭제 뒤 원래 데이터 위치를 확인하지 못했습니다."
        )
    return {"restored_axis": axis, "restored_count": count}


class StructureInsertOperation(ExcelOperation):
    """Rows and columns differ only by axis, so they share one preparation."""

    axis = "row"

    def prepare(self, adapter, application, params):
        axis = self.axis
        _, sheet, base = adapter._common_context(application)
        selection = normalize_range_address(params.get("selection_range"))
        first_column, first_row, _, _ = adapter._range_bounds(selection)
        try:
            count = int(params.get("count", 1))
        except (TypeError, ValueError) as error:
            raise AppActionBlocked("추가할 행·열 개수는 숫자여야 합니다.") from error
        if not 1 <= count <= 10:
            raise AppActionBlocked("행·열 추가는 한 번에 1개부터 10개까지 지원합니다.")

        used = sheet.UsedRange
        used_address = adapter._address(used)
        used_first_column, used_first_row, used_last_column, used_last_row = (
            adapter._range_bounds(used_address)
        )
        used_count = adapter._range_count(used)
        if used_count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"행·열 추가는 사용 영역이 {MAX_SOURCE_CELLS:,}개 셀 이하인 시트에서만 지원합니다."
            )
        index = first_row if axis == "row" else first_column
        lower = used_first_row if axis == "row" else used_first_column
        upper = used_last_row if axis == "row" else used_last_column
        if not lower <= index <= upper:
            raise AppActionBlocked("데이터가 있는 사용 영역 안의 행 또는 열을 선택해주세요.")

        if axis == "row":
            shifted_address = (
                f"{excel_column_letters(used_first_column)}{index}:"
                f"{excel_column_letters(used_last_column)}{used_last_row}"
            )
            target = f"{index}행"
        else:
            shifted_address = (
                f"{excel_column_letters(index)}{used_first_row}:"
                f"{excel_column_letters(used_last_column)}{used_last_row}"
            )
            target = f"{excel_column_letters(index)}열"
        shifted_digest = adapter._range_digest(sheet.Range(shifted_address))
        snapshot = {
            **base,
            "operation": self.name,
            "selection": selection,
            "axis": axis,
            "index": index,
            "count": count,
            "used_address": used_address,
            "used_digest": adapter._range_digest(used),
            "shifted_address": shifted_address,
            "shifted_digest": shifted_digest,
        }
        affected = (
            (used_last_row - index + 1)
            * (used_last_column - used_first_column + 1)
            if axis == "row"
            else (used_last_column - index + 1)
            * (used_last_row - used_first_row + 1)
        )
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=target,
            params={
                "selection_range": selection,
                "axis": axis,
                "index": index,
                "count": count,
                "used_first_column": used_first_column,
                "used_first_row": used_first_row,
                "used_last_column": used_last_column,
                "used_last_row": used_last_row,
                "shifted_digest": shifted_digest,
            },
            current_state={
                "used_address": used_address,
                "affected_cells": affected,
            },
            estimated_changes=affected,
            destructive=True,
            reversible=True,
            verification_method=(
                "verify_shifted_rows" if axis == "row" else "verify_shifted_columns"
            ),
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        axis = current.params["axis"]
        index = int(current.params["index"])
        count = int(current.params["count"])
        first_column = int(current.params["used_first_column"])
        first_row = int(current.params["used_first_row"])
        last_column = int(current.params["used_last_column"])
        last_row = int(current.params["used_last_row"])
        inserted = None
        try:
            if axis == "row":
                inserted = sheet.Rows(f"{index}:{index + count - 1}")
                inserted.Insert()
                shifted = sheet.Range(
                    f"{excel_column_letters(first_column)}{index + count}:"
                    f"{excel_column_letters(last_column)}{last_row + count}"
                )
                blank = sheet.Range(
                    f"{excel_column_letters(first_column)}{index}:"
                    f"{excel_column_letters(last_column)}{index + count - 1}"
                )
            else:
                first = excel_column_letters(index)
                last = excel_column_letters(index + count - 1)
                inserted = sheet.Columns(f"{first}:{last}")
                inserted.Insert()
                shifted = sheet.Range(
                    f"{excel_column_letters(index + count)}{first_row}:"
                    f"{excel_column_letters(last_column + count)}{last_row}"
                )
                blank = sheet.Range(f"{first}{first_row}:{last}{last_row}")
            if adapter._range_digest(shifted) != current.params["shifted_digest"]:
                raise AppActionVerificationError(
                    "Excel 행·열 추가 후 기존 데이터의 이동 결과가 예상과 다릅니다."
                )
            if not adapter._range_is_blank(blank):
                raise AppActionVerificationError(
                    "Excel에 추가된 행·열이 비어 있지 않아 결과를 승인할 수 없습니다."
                )
        except Exception as error:
            if inserted is not None:
                try:
                    inserted.Delete()
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 행·열 추가 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"used_address": current.current_state["used_address"]},
            {"inserted": count, "axis": axis},
            changed=True,
        )

    undo = staticmethod(undo_structure_insert)


class InsertRowsOperation(StructureInsertOperation):
    name = "insert_rows"
    axis = "row"


class InsertColumnsOperation(StructureInsertOperation):
    name = "insert_columns"
    axis = "column"


__all__ = [
    "InsertColumnsOperation",
    "InsertRowsOperation",
    "StructureInsertOperation",
    "undo_structure_insert",
]
