"""Sort an Excel table by one column and prove every row moved intact."""

from __future__ import annotations

import json

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import MAX_SORT_ROWS, ExcelOperation
from engine.app_actions.value_normalizer import (
    excel_column_letters,
    excel_formulas_equal,
    serializable_excel_value,
)

XL_ASCENDING = 1
XL_DESCENDING = 2
XL_NO = 2
XL_SORT_COLUMNS = 1


def row_multiset(rows):
    """Order-independent fingerprint proving no row was gained or lost."""
    return sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )


def sort_key_kind(values):
    nonblank = [value for value in values if value not in {None, ""}]
    if not nonblank:
        raise AppActionBlocked("정렬 기준 열에 데이터가 없습니다.")
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in nonblank
    ):
        return "number"
    if all(isinstance(value, str) for value in nonblank):
        return "text"
    raise AppActionBlocked("정렬 기준 열에 숫자와 문자가 섞여 있어 자동 정렬하지 않습니다.")


def sorted_keys(values, kind, descending):
    nonblank = [value for value in values if value not in {None, ""}]
    blanks = [value for value in values if value in {None, ""}]
    key = (lambda value: float(value)) if kind == "number" else (
        lambda value: value.casefold()
    )
    return sorted(nonblank, key=key, reverse=descending) + blanks


def table_values(sheet, table):
    values = []
    for row in range(2, table["last_row"] + 1):
        values.append([
            serializable_excel_value(sheet.Cells(row, column).Value2)
            for column in range(table["first_column"], table["last_column"] + 1)
        ])
    return values


def undo_sort(adapter, application, prepared):
    sheet = adapter._ensure_undo_context(application, prepared)
    table = adapter._resolve_table_range(
        sheet,
        {"table_range": prepared.target},
        prepared.params.get("column_name"),
    )
    current_rows = table_values(sheet, table)
    key_index = table["column_number"] - table["first_column"]
    if (
        row_multiset(current_rows)
        != row_multiset(prepared.params["original_rows"])
        or [row[key_index] for row in current_rows]
        != prepared.params["expected_keys"]
    ):
        raise AppActionContextChanged(
            "직전 정렬 뒤 표 데이터가 달라져 안전하게 복원하지 않았습니다."
        )

    original_cells = list(prepared.params.get("original_cells") or [])
    for row_offset, original_row in enumerate(prepared.params["original_rows"]):
        row_number = row_offset + 2
        for column_offset, value in enumerate(original_row):
            column_number = table["first_column"] + column_offset
            cell = sheet.Cells(row_number, column_number)
            if original_cells:
                adapter._restore_cell_value(
                    cell,
                    original_cells[row_offset][column_offset],
                )
            else:
                cell.Value2 = value
    restored_rows = table_values(sheet, table)
    if restored_rows != prepared.params["original_rows"]:
        raise AppActionVerificationError(
            "Excel 표의 정렬 전 행 순서가 복원되었는지 확인하지 못했습니다."
        )
    if original_cells:
        for row_offset, row_snapshot in enumerate(original_cells):
            for column_offset, snapshot in enumerate(row_snapshot):
                cell = sheet.Cells(
                    row_offset + 2,
                    table["first_column"] + column_offset,
                )
                formula = str(cell.Formula) if bool(cell.HasFormula) else None
                if not excel_formulas_equal(formula, snapshot.get("formula")):
                    raise AppActionVerificationError(
                        "Excel 표의 정렬 전 수식이 복원되었는지 확인하지 못했습니다."
                    )
    return {"restored_rows": len(restored_rows)}


class SortRangeOperation(ExcelOperation):
    name = "sort_range"

    def prepare(self, adapter, application, params):
        _, sheet, base = adapter._common_context(application)
        if bool(getattr(sheet, "FilterMode", False)):
            raise AppActionBlocked("필터가 적용된 표는 필터를 해제한 뒤 정렬해주세요.")
        column_name = params.get("column_name")
        table = adapter._resolve_table_range(sheet, params, column_name)
        if table["last_row"] - 1 > MAX_SORT_ROWS:
            raise AppActionBlocked(
                f"정렬은 한 번에 최대 {MAX_SORT_ROWS:,}개 데이터 행까지 지원합니다."
            )
        if bool(getattr(table["range"], "MergeCells", False)):
            raise AppActionBlocked("병합된 셀이 포함된 표는 자동 정렬하지 않습니다.")
        direction = str(params.get("direction") or "ascending").strip().casefold()
        aliases = {
            "asc": "ascending", "오름차순": "ascending", "낮은순": "ascending",
            "desc": "descending", "내림차순": "descending", "높은순": "descending",
        }
        direction = aliases.get(direction, direction)
        if direction not in {"ascending", "descending"}:
            raise AppActionBlocked("정렬 방향은 오름차순 또는 내림차순이어야 합니다.")
        rows = table_values(sheet, table)
        original_cells = []
        for row_number in range(2, table["last_row"] + 1):
            row_snapshot = []
            for column_number in range(
                table["first_column"], table["last_column"] + 1
            ):
                cell = sheet.Cells(row_number, column_number)
                has_formula = bool(getattr(cell, "HasFormula", False))
                row_snapshot.append({
                    "formula": str(cell.Formula) if has_formula else None,
                    "value": serializable_excel_value(cell.Value2),
                })
            original_cells.append(row_snapshot)
        key_index = table["column_number"] - table["first_column"]
        keys = [row[key_index] for row in rows]
        kind = sort_key_kind(keys)
        expected = sorted_keys(keys, kind, direction == "descending")
        noop = keys == expected
        snapshot = {
            **base,
            "operation": self.name,
            "target": table["address"],
            "table_digest": adapter._range_digest(table["range"]),
            "column_number": table["column_number"],
            "direction": direction,
            "rows": rows,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=table["address"],
            params={
                "table_range": table["address"],
                "column_name": column_name,
                "column_number": table["column_number"],
                "direction": direction,
                "key_kind": kind,
                "expected_keys": expected,
                "original_rows": rows,
                "original_cells": original_cells,
            },
            current_state={
                "row_count": len(rows),
                "column_count": table["last_column"] - table["first_column"] + 1,
            },
            estimated_changes=0 if noop else len(rows),
            destructive=not noop,
            reversible=True,
            verification_method="read_sorted_table",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        before = {
            "row_count": current.current_state["row_count"],
            "column_count": current.current_state["column_count"],
        }
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        first_column, _, last_column, last_row = adapter._range_bounds(current.target)
        column = excel_column_letters(current.params["column_number"])
        # Excel may ignore Header=xlYes when Key1 itself includes the header
        # cell, which can move the header into the sorted data on real Office
        # installations.  Exclude the header from both the source and key so
        # it is structurally impossible for the header row to move.
        key_range = sheet.Range(f"{column}2:{column}{last_row}")
        source = sheet.Range(
            f"{excel_column_letters(first_column)}2:"
            f"{excel_column_letters(last_column)}{last_row}"
        )
        sort_completed = False
        try:
            source.Sort(
                Key1=key_range,
                Order1=(
                    XL_DESCENDING
                    if current.params["direction"] == "descending"
                    else XL_ASCENDING
                ),
                Header=XL_NO,
                Orientation=XL_SORT_COLUMNS,
            )
            sort_completed = True
            table = adapter._resolve_table_range(
                sheet,
                {"table_range": current.target},
                current.params["column_name"],
            )
            rows = table_values(sheet, table)
            key_index = table["column_number"] - table["first_column"]
            actual_keys = [row[key_index] for row in rows]
            if actual_keys != current.params["expected_keys"]:
                raise AppActionVerificationError("Excel 정렬 순서가 요청과 다릅니다.")
            if row_multiset(rows) != row_multiset(current.params["original_rows"]):
                raise AppActionVerificationError(
                    "정렬 후 표의 행 데이터 구성이 달라졌습니다."
                )
            after = {
                "row_count": len(rows),
                "direction": current.params["direction"],
                "verified_whole_rows": True,
            }
        except Exception as error:
            if sort_completed:
                try:
                    application.Undo()
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 표 정렬 실행 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)

    undo = staticmethod(undo_sort)


__all__ = ["SortRangeOperation", "table_values", "undo_sort"]
