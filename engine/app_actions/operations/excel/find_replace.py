"""Replace literal text in Excel cells, never touching formulas."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import (
    MAX_FIND_REPLACE_CELLS,
    ExcelOperation,
)
from engine.app_actions.value_normalizer import (
    excel_values_equal,
    normalize_range_address,
)


def undo_find_replace(adapter, application, prepared):
    sheet = adapter._ensure_undo_context(application, prepared)
    matches = list(prepared.params.get("matches") or [])
    for match in matches:
        cell = sheet.Range(match["address"])
        if not excel_values_equal(cell.Value2, match["replacement"]):
            raise AppActionContextChanged(
                "직전 찾기·바꾸기 뒤 셀 값이 달라져 복원하지 않았습니다."
            )
    for match in matches:
        sheet.Range(match["address"]).Value2 = match["original"]
    for match in matches:
        if not excel_values_equal(
            sheet.Range(match["address"]).Value2,
            match["original"],
        ):
            raise AppActionVerificationError(
                f"Excel {match['address']} 셀의 원래 값을 확인하지 못했습니다."
            )
    return {"restored_count": len(matches)}


class FindReplaceOperation(ExcelOperation):
    name = "find_replace"

    def prepare(self, adapter, application, params):
        _, sheet, base = adapter._common_context(application)
        scope = str(params.get("scope") or "range").strip().casefold()
        if scope not in {"range", "current_sheet"}:
            raise AppActionBlocked(
                "찾기·바꾸기는 지정 범위 또는 현재 시트에서만 지원합니다."
            )
        if scope == "current_sheet":
            address = normalize_range_address(adapter._address(sheet.UsedRange))
        else:
            address = normalize_range_address(params.get("range"))
        source = sheet.Range(address)
        count = adapter._range_count(source)
        if count > MAX_FIND_REPLACE_CELLS:
            raise AppActionBlocked(
                f"찾기·바꾸기는 한 번에 최대 {MAX_FIND_REPLACE_CELLS:,}개 셀까지 지원합니다."
            )
        old = str(params.get("find") if params.get("find") is not None else "")
        new = str(
            params.get("replace") if params.get("replace") is not None else ""
        )
        if old == "":
            raise AppActionBlocked("찾을 문자열은 비워둘 수 없습니다.")
        whole_cell = bool(params.get("whole_cell"))
        match_case = bool(params.get("match_case"))
        matches = []
        for cell in adapter._range_cells(source):
            if bool(getattr(cell, "MergeCells", False)):
                raise AppActionBlocked(
                    "병합된 셀이 포함된 범위에서는 찾기·바꾸기를 실행하지 않습니다."
                )
            if bool(getattr(cell, "HasFormula", False)):
                continue
            value = cell.Value2
            if not isinstance(value, str):
                continue
            replacement = adapter._replace_text(
                value, old, new, whole_cell, match_case
            )
            if replacement is not None and replacement != value:
                matches.append({
                    "address": adapter._address(cell),
                    "original": value,
                    "replacement": replacement,
                })
        snapshot = {
            **base,
            "operation": self.name,
            "target": address,
            "scope": scope,
            "range_digest": adapter._range_digest(source),
            "find": old,
            "replace": new,
            "whole_cell": whole_cell,
            "match_case": match_case,
            "matches": matches,
        }
        noop = not matches
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=address,
            params={
                "scope": scope,
                "range": address,
                "find": old,
                "replace": new,
                "whole_cell": whole_cell,
                "match_case": match_case,
                "matches": matches,
            },
            current_state={"cell_count": count, "matching_count": len(matches)},
            estimated_changes=len(matches),
            destructive=bool(matches),
            reversible=True,
            verification_method="read_replaced_cell_values",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        before = {"matching_count": current.current_state["matching_count"]}
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        changed = []
        try:
            for match in current.params["matches"]:
                cell = sheet.Range(match["address"])
                cell.Value2 = match["replacement"]
                changed.append((cell, match))
            for cell, match in changed:
                if not excel_values_equal(cell.Value2, match["replacement"]):
                    raise AppActionVerificationError(
                        f"Excel {match['address']} 셀의 바꾸기 결과가 요청과 다릅니다."
                    )
            after = {"replaced_count": len(changed)}
        except Exception as error:
            for cell, match in reversed(changed):
                try:
                    cell.Value2 = match["original"]
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 찾기·바꾸기 실행 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)

    undo = staticmethod(undo_find_replace)


__all__ = ["FindReplaceOperation", "undo_find_replace"]
