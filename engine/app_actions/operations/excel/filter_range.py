"""Apply or clear one Excel AutoFilter criterion."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import ExcelOperation
from engine.app_actions.value_normalizer import (
    normalize_excel_input,
    normalize_range_address,
)


def undo_filter(adapter, application, prepared):
    sheet = adapter._ensure_undo_context(application, prepared)
    current = adapter._filter_state(sheet)
    if prepared.params.get("clear"):
        matches_after = not current.get("filter_mode")
    else:
        matches_after = adapter._matching_filter(
            current,
            prepared.params["table_range"],
            prepared.params["field_index"],
            prepared.params["criteria"],
        )
    if not matches_after:
        raise AppActionContextChanged(
            "직전 편집 뒤 필터 상태가 달라져 안전하게 복원하지 않았습니다."
        )
    previous = dict(prepared.params.get("previous_filter") or {})
    adapter._restore_filter_state(sheet, previous)
    restored = adapter._filter_state(sheet)
    if adapter._state_fingerprint({"filter": restored}) != adapter._state_fingerprint(
        {"filter": previous}
    ):
        raise AppActionVerificationError(
            "Excel의 이전 필터 상태가 복원되었는지 확인하지 못했습니다."
        )
    return {"filter_state": restored}


class FilterRangeOperation(ExcelOperation):
    name = "filter_range"

    def prepare(self, adapter, application, params):
        _, sheet, base = adapter._common_context(application)
        previous = adapter._filter_state(sheet)
        clear = bool(params.get("clear"))
        if clear:
            target = previous.get("range")
            if not target:
                target = normalize_range_address(adapter._address(sheet.UsedRange))
            snapshot = {
                **base,
                "operation": self.name,
                "target": target,
                "filter_state": previous,
                "clear": True,
            }
            noop = not previous.get("filter_mode")
            return PreparedAction(
                app=self.app,
                operation=self.name,
                document_id=base["document_id"],
                workbook_name=base["workbook_name"],
                sheet=base["sheet"],
                target=target,
                params={"clear": True, "previous_filter": previous},
                current_state={
                    "filter_active": bool(previous.get("filter_mode")),
                    "active_filter_count": sum(
                        1 for item in previous.get("filters", []) if item.get("on")
                    ),
                },
                estimated_changes=0 if noop else 1,
                destructive=False,
                reversible=True,
                verification_method="read_autofilter_state",
                context_fingerprint=adapter._state_fingerprint(snapshot),
                prepared_at=adapter._created_at(),
                noop=noop,
                metadata={"application_hwnd": base["application_hwnd"]},
            )

        column_name = params.get("column_name")
        table = adapter._resolve_table_range(sheet, params, column_name)
        operator = adapter._normalize_operator(params.get("operator") or "eq")
        normalized = normalize_excel_input(params.get("value"))
        if normalized["kind"] == "formula":
            raise AppActionBlocked("필터 값에는 수식을 사용할 수 없습니다.")
        value = normalized["value"]
        criteria = adapter._filter_criteria(operator, value)
        noop = adapter._matching_filter(
            previous, table["address"], table["field_index"], criteria
        )
        active_count = sum(
            1 for item in previous.get("filters", []) if item.get("on")
        )
        snapshot = {
            **base,
            "operation": self.name,
            "target": table["address"],
            "table_digest": adapter._range_digest(table["range"]),
            "filter_state": previous,
            "field_index": table["field_index"],
            "criteria": criteria,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=table["address"],
            params={
                "clear": False,
                "table_range": table["address"],
                "column_name": column_name,
                "field_index": table["field_index"],
                "operator": operator,
                "value": value,
                "criteria": criteria,
                "previous_filter": previous,
            },
            current_state={
                "row_count": table["last_row"] - 1,
                "active_filter_count": active_count,
                "filter_active": bool(previous.get("filter_mode")),
            },
            estimated_changes=0 if noop else table["last_row"] - 1,
            destructive=active_count > 0 and not noop,
            reversible=True,
            verification_method="read_autofilter_state",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        previous = current.params["previous_filter"]
        before = {
            "filter_active": current.current_state["filter_active"],
            "active_filter_count": current.current_state["active_filter_count"],
        }
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        try:
            if current.params["clear"]:
                sheet.ShowAllData()
                after_state = adapter._filter_state(sheet)
                if after_state.get("filter_mode"):
                    raise AppActionVerificationError(
                        "Excel 필터 해제 결과를 확인하지 못했습니다."
                    )
            else:
                source = sheet.Range(current.params["table_range"])
                source.AutoFilter(
                    Field=current.params["field_index"],
                    Criteria1=current.params["criteria"],
                )
                after_state = adapter._filter_state(sheet)
                if not adapter._matching_filter(
                    after_state,
                    current.params["table_range"],
                    current.params["field_index"],
                    current.params["criteria"],
                ):
                    raise AppActionVerificationError(
                        "Excel 필터 적용 결과를 확인하지 못했습니다."
                    )
            after = {
                "filter_active": bool(after_state.get("filter_mode")),
                "active_filter_count": sum(
                    1 for item in after_state.get("filters", []) if item.get("on")
                ),
            }
        except Exception as error:
            try:
                adapter._restore_filter_state(sheet, previous)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 필터 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)

    undo = staticmethod(undo_filter)


__all__ = ["FilterRangeOperation", "undo_filter"]
