"""Aggregate an Excel column into one cell as a formula or a fixed value."""

from __future__ import annotations

from engine.app_actions.base import AppActionBlocked, PreparedAction
from engine.app_actions.operations.excel.base import MAX_SOURCE_CELLS, ExcelOperation
from engine.app_actions.operations.excel.write_cell import undo_cell_write
from engine.app_actions.value_normalizer import normalize_cell_address


class SumColumnToCellOperation(ExcelOperation):
    name = "sum_column_to_cell"

    def prepare(self, adapter, application, params):
        _, sheet, base = adapter._common_context(application)
        source, source_address, header = adapter._resolve_source_range(sheet, params)
        count = adapter._range_count(source)
        if count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"한 번에 최대 {MAX_SOURCE_CELLS:,}개 셀까지 집계할 수 있습니다."
            )
        target_address = normalize_cell_address(
            params.get("target_cell") or params.get("cell")
        )
        if adapter._cell_in_range(target_address, source_address):
            raise AppActionBlocked(
                "집계 결과 셀이 원본 범위 안에 있어 순환 참조가 생길 수 있습니다."
            )
        target = sheet.Range(target_address)
        if bool(getattr(target, "MergeCells", False)):
            raise AppActionBlocked("병합된 셀에는 집계 결과를 입력하지 않았습니다.")
        target_state = adapter._cell_snapshot(target)
        mode = str(
            params.get("result_mode") or params.get("write_mode") or "formula"
        ).strip().casefold()
        if mode not in {"formula", "value"}:
            raise AppActionBlocked("집계 결과 방식은 formula 또는 value여야 합니다.")
        aggregation = str(params.get("aggregation") or "sum").strip().casefold()
        if aggregation not in {"sum", "average"}:
            raise AppActionBlocked("지원하는 집계 방식은 합계와 평균입니다.")
        function_name = "AVERAGE" if aggregation == "average" else "SUM"
        desired = (
            {"kind": "formula", "value": f"={function_name}({source_address})"}
            if mode == "formula"
            else {
                "kind": "value",
                "value": adapter._fixed_aggregate(application, source, aggregation),
            }
        )
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._address(target),
            **target_state,
            "source_range": source_address,
            "source_digest": adapter._range_digest(source),
            "source_count": count,
            "result_mode": mode,
            "aggregation": aggregation,
        }
        is_empty = adapter._is_empty(snapshot)
        noop = adapter._matches_desired(snapshot, desired)
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=snapshot["target"],
            params={
                "source_range": source_address,
                "column_name": header,
                "target_cell": snapshot["target"],
                "result_mode": mode,
                "aggregation": aggregation,
                "value": desired["value"],
                "value_type": desired["kind"],
            },
            current_state={
                "formula": target_state["current_formula"],
                "value": target_state["current_value"],
                "empty": is_empty,
                "source_range": source_address,
                "source_count": count,
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
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        cell = sheet.Range(current.target)
        before = {
            "current_formula": current.current_state.get("formula"),
            "current_value": current.current_state.get("value"),
        }
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        desired = {
            "kind": current.params["value_type"],
            "value": current.params["value"],
        }
        return adapter._write_and_verify(cell, before, desired, current)

    undo = staticmethod(undo_cell_write)


__all__ = ["SumColumnToCellOperation"]
