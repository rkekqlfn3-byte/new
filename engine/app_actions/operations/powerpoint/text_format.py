"""Apply bold and font size to the selected PowerPoint text."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.app_actions.operations.powerpoint.base import PowerPointOperation
from engine.app_actions.operations.powerpoint.state import uniform_style_value


class SetTextFormatOperation(PowerPointOperation):
    name = "set_text_format"

    def desired_format(self, params, state):
        if state["style"] is None:
            raise AppActionBlocked("선택한 Shape에 텍스트 서식을 적용할 수 없습니다.")
        prepared_desired = params.get("desired")
        if isinstance(prepared_desired, dict):
            desired = {}
            if prepared_desired.get("bold") is not None:
                uniform_style_value(state["style"]["bold"], "굵기")
                desired["bold"] = -1 if int(prepared_desired["bold"]) != 0 else 0
            if prepared_desired.get("font_size") is not None:
                uniform_style_value(state["style"]["font_size"], "글자 크기")
                size = float(prepared_desired["font_size"])
                if not 1 <= size <= 4000:
                    raise AppActionBlocked("PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
                desired["font_size"] = size
            if desired:
                return desired
        desired = {}
        if params.get("bold") is not None:
            uniform_style_value(state["style"]["bold"], "굵기")
            desired["bold"] = -1 if bool(params["bold"]) else 0
        if params.get("font_size") is not None:
            uniform_style_value(state["style"]["font_size"], "글자 크기")
            size = float(params["font_size"])
            if not 1 <= size <= 4000:
                raise AppActionBlocked("PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        elif params.get("font_size_delta") is not None:
            current = uniform_style_value(state["style"]["font_size"], "글자 크기")
            size = current + float(params["font_size_delta"])
            if not 1 <= size <= 4000:
                raise AppActionBlocked("변경할 PowerPoint 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        if not desired:
            raise AppActionBlocked("변경할 PowerPoint 글자 서식을 지정해주세요.")
        return desired

    def prepare(self, adapter, session, params):
        _, _, state = adapter._context(session.application, session.presentation)
        desired = self.desired_format(params, state)
        current = {
            "bold": state["style"]["bold"],
            "font_size": state["style"]["font_size"],
        }
        noop = all(current[key] == value for key, value in desired.items())
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "desired": desired,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet=f"슬라이드 {state['slide_index']}",
            target=adapter._target(state),
            params={
                "document_path": state["document_id"],
                "desired": desired,
                "original": current,
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=0 if noop else max(1, len(state["selected_text"])),
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_font",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, target, current):
        text_range = adapter._range_for_prepared(target.shape, current)
        desired = current.params["desired"]
        try:
            adapter._apply_font(text_range, desired)
            if "bold" in desired and int(text_range.Font.Bold) != int(desired["bold"]):
                raise AppActionVerificationError("PowerPoint 굵기 적용 결과가 다릅니다.")
            if "font_size" in desired and abs(
                float(text_range.Font.Size) - float(desired["font_size"])
            ) > 0.01:
                raise AppActionVerificationError(
                    "PowerPoint 글자 크기 적용 결과가 다릅니다."
                )
        except Exception as error:
            try:
                adapter._apply_font(text_range, current.params["original"])
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"format": desired})


__all__ = ["SetTextFormatOperation"]
