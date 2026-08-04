"""Apply bold, size, colour and alignment to an Excel range."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import (
    MAX_RANGE_FORMAT_CELLS,
    ExcelOperation,
)
from engine.app_actions.value_normalizer import normalize_range_address


def undo_range_format(adapter, application, prepared):
    sheet = adapter._ensure_undo_context(application, prepared)
    desired = dict(prepared.params.get("desired") or {})
    originals = list(prepared.params.get("original_formats") or [])
    for original in originals:
        current = adapter._format_state(sheet.Range(original["address"]))
        if not adapter._format_state_matches(current, desired):
            raise AppActionContextChanged(
                "직전 편집 뒤 범위 서식이 달라져 안전하게 복원하지 않았습니다."
            )
    for original in originals:
        adapter._restore_format(sheet.Range(original["address"]), original)
    restored = []
    for original in originals:
        state = adapter._format_state(sheet.Range(original["address"]))
        if not adapter._format_state_matches(state, original):
            raise AppActionVerificationError(
                f"Excel {original['address']} 셀의 원래 서식을 확인하지 못했습니다."
            )
        restored.append(state)
    return {"restored_count": len(restored)}


class FormatRangeOperation(ExcelOperation):
    name = "format_range"

    def desired_format(self, adapter, params):
        supplied = params.get("desired")
        desired = {}
        labels = {}
        if isinstance(supplied, dict):
            for key in {
                "bold", "font_size", "font_color", "fill_color", "alignment"
            }:
                if key in supplied:
                    desired[key] = supplied[key]
            labels = dict(params.get("format_labels") or {})
        else:
            if params.get("bold") is not None:
                desired["bold"] = bool(params.get("bold"))
            if params.get("font_size") is not None:
                try:
                    size = float(params.get("font_size"))
                except (TypeError, ValueError) as error:
                    raise AppActionBlocked("글자 크기는 숫자로 지정해주세요.") from error
                if not 1 <= size <= 409:
                    raise AppActionBlocked("글자 크기는 1부터 409 사이여야 합니다.")
                desired["font_size"] = int(size) if size.is_integer() else size
            if params.get("alignment") is not None:
                label, value = adapter._normalize_alignment(params.get("alignment"))
                desired["alignment"] = value
                labels["alignment"] = label
            for parameter, key in (
                ("font_color", "font_color"),
                ("fill_color", "fill_color"),
            ):
                if params.get(parameter) is not None:
                    label, value = adapter._normalize_color(params.get(parameter))
                    desired[key] = value
                    labels[key] = label
        if not desired:
            raise AppActionBlocked("적용할 범위 서식을 하나 이상 지정해주세요.")
        allowed = {"bold", "font_size", "font_color", "fill_color", "alignment"}
        if set(desired) - allowed:
            raise AppActionBlocked("지원하지 않는 범위 서식이 포함되어 있습니다.")
        if "font_size" in desired:
            try:
                size = float(desired["font_size"])
            except (TypeError, ValueError) as error:
                raise AppActionBlocked("글자 크기는 숫자로 지정해주세요.") from error
            if not 1 <= size <= 409:
                raise AppActionBlocked("글자 크기는 1부터 409 사이여야 합니다.")
            desired["font_size"] = int(size) if size.is_integer() else size
        if "bold" in desired:
            desired["bold"] = bool(desired["bold"])
        for key in {"font_color", "fill_color", "alignment"} & set(desired):
            try:
                desired[key] = int(desired[key])
            except (TypeError, ValueError) as error:
                raise AppActionBlocked("범위 서식 값이 올바르지 않습니다.") from error
        return desired, labels

    def prepare(self, adapter, application, params):
        _, sheet, base = adapter._common_context(application)
        address = normalize_range_address(params.get("range"))
        source = sheet.Range(address)
        count = adapter._range_count(source)
        if count > MAX_RANGE_FORMAT_CELLS:
            raise AppActionBlocked(
                f"범위 서식은 한 번에 최대 {MAX_RANGE_FORMAT_CELLS:,}개 셀까지 지원합니다."
            )
        desired, labels = self.desired_format(adapter, params)
        states = []
        changing = []
        for cell in adapter._range_cells(source):
            if bool(getattr(cell, "MergeCells", False)):
                raise AppActionBlocked(
                    "병합된 셀이 포함된 범위에는 안전하게 서식을 적용하지 않습니다."
                )
            state = adapter._format_state(cell)
            states.append(state)
            if not adapter._format_state_matches(state, desired):
                changing.append(state)
        snapshot = {
            **base,
            "operation": self.name,
            "target": address,
            "source_digest": adapter._range_digest(source),
            "formats": states,
            "desired": desired,
        }
        noop = not changing
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=address,
            params={
                "range": address,
                "desired": desired,
                "format_labels": labels,
                "original_formats": changing,
            },
            current_state={
                "cell_count": count,
                "changing_count": len(changing),
            },
            estimated_changes=len(changing),
            destructive=len(changing) > 100,
            reversible=True,
            verification_method="read_cell_formats",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        source = sheet.Range(current.target)
        before = {
            "cell_count": current.current_state["cell_count"],
            "changing_count": current.current_state["changing_count"],
        }
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        try:
            adapter._apply_desired_format(source, current.params["desired"])
            for cell in adapter._range_cells(source):
                state = adapter._format_state(cell)
                if not adapter._format_state_matches(state, current.params["desired"]):
                    raise AppActionVerificationError(
                        f"Excel {state['address']} 셀의 서식 적용 결과가 요청과 다릅니다."
                    )
            after = {
                "cell_count": current.current_state["cell_count"],
                "changed_count": current.current_state["changing_count"],
                "desired": current.params["desired"],
            }
        except Exception as error:
            for original in reversed(current.params["original_formats"]):
                try:
                    adapter._restore_format(sheet.Range(original["address"]), original)
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 범위 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)

    undo = staticmethod(undo_range_format)


__all__ = ["FormatRangeOperation", "undo_range_format"]
