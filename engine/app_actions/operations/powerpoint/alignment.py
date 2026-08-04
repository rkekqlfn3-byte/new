"""Align the selected PowerPoint text."""

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
from engine.app_actions.operations.powerpoint.state import (
    normalize_alignment,
    uniform_style_value,
)


class SetTextAlignmentOperation(PowerPointOperation):
    name = "set_text_alignment"

    def prepare(self, adapter, session, params):
        _, _, state = adapter._context(session.application, session.presentation)
        if state["style"] is None:
            raise AppActionBlocked("선택한 Shape에 문단 정렬을 적용할 수 없습니다.")
        uniform_style_value(state["style"]["alignment"], "문단 정렬")
        label, alignment = normalize_alignment(params.get("alignment"))
        current = int(state["style"]["alignment"])
        noop = current == alignment
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "desired_alignment": alignment,
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
                "alignment": alignment,
                "alignment_label": label,
                "original_alignment": current,
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=0 if noop else 1,
            destructive=False,
            reversible=True,
            verification_method="read_powerpoint_paragraph_alignment",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, target, current):
        text_range = adapter._range_for_prepared(target.shape, current)
        desired = int(current.params["alignment"])
        try:
            text_range.ParagraphFormat.Alignment = desired
            if int(text_range.ParagraphFormat.Alignment) != desired:
                raise AppActionVerificationError(
                    "PowerPoint 텍스트 정렬 결과가 다릅니다."
                )
        except Exception as error:
            try:
                text_range.ParagraphFormat.Alignment = int(
                    current.params["original_alignment"]
                )
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 텍스트 정렬 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"alignment": desired})


__all__ = ["SetTextAlignmentOperation"]
