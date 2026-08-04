"""Copy the text style of the same role on the previous slide."""

from __future__ import annotations

import re

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
from engine.app_actions.operations.powerpoint.base import (
    MSO_PLACEHOLDER,
    PowerPointOperation,
)


def role_name(name):
    """Strip a trailing index so `Title 3` and `Title 7` share a role."""
    return re.sub(r"[\s_-]*\d+$", "", str(name or "").strip().casefold())


class MatchPreviousStyleOperation(PowerPointOperation):
    name = "match_previous_style"

    def reference_shape(self, adapter, presentation, state):
        if state["slide_index"] <= 1:
            raise AppActionBlocked("앞 슬라이드가 없어 스타일을 참조할 수 없습니다.")
        slide = presentation.Slides.Item(state["slide_index"] - 1)
        matches = []
        for index in range(1, int(slide.Shapes.Count) + 1):
            shape = slide.Shapes.Item(index)
            placeholder = None
            try:
                if int(shape.Type) == MSO_PLACEHOLDER:
                    placeholder = int(shape.PlaceholderFormat.Type)
            except Exception:
                pass
            same_role = (
                state["placeholder_type"] is not None
                and placeholder == state["placeholder_type"]
            ) or (
                state["placeholder_type"] is None
                and role_name(shape.Name) == role_name(state["shape_name"])
            )
            if same_role and adapter._has_text_frame(shape):
                matches.append(shape)
        if len(matches) != 1:
            raise AppActionBlocked(
                "앞 슬라이드에서 같은 역할의 텍스트 Shape를 하나로 특정하지 못했습니다."
            )
        return slide, matches[0]

    def prepare(self, adapter, session, params):
        presentation = session.presentation
        _, _, state = adapter._context(session.application, presentation)
        if state["style"] is None:
            raise AppActionBlocked("현재 Shape에 참조 스타일을 적용할 텍스트가 없습니다.")
        source_slide, source_shape = self.reference_shape(adapter, presentation, state)
        source_range = source_shape.TextFrame.TextRange
        desired = adapter._style_state(source_range)
        original = dict(state["style"])
        noop = original == desired
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "source_slide_id": int(source_slide.SlideID),
            "source_shape_id": int(source_shape.Id),
            "desired_style": desired,
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
                "original": original,
                "source_slide_id": int(source_slide.SlideID),
                "source_shape_id": int(source_shape.Id),
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=0 if noop else max(1, len(state["selected_text"])),
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_style",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, target, current):
        text_range = adapter._range_for_prepared(target.shape, current)
        desired = current.params["desired"]
        try:
            adapter._apply_style(text_range, desired)
            actual = adapter._style_state(text_range)
            if actual != desired:
                raise AppActionVerificationError(
                    "PowerPoint 앞 슬라이드 스타일 적용 결과가 다릅니다."
                )
        except Exception as error:
            try:
                adapter._apply_style(text_range, current.params["original"])
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 참조 스타일 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"style": desired})


__all__ = ["MatchPreviousStyleOperation", "role_name"]
