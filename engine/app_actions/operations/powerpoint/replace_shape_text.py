"""Replace the text of the selected PowerPoint shape or text run."""

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
from engine.app_actions.operations.powerpoint.base import (
    MAX_PPT_TEXT_CHARS,
    PPT_SELECTION_SHAPES,
    PowerPointOperation,
)


class ReplaceShapeTextOperation(PowerPointOperation):
    name = "replace_shape_text"

    def prepare(self, adapter, session, params):
        _, _, state = adapter._context(session.application, session.presentation)
        if not state["has_text_frame"]:
            raise AppActionBlocked("선택한 PowerPoint Shape에는 편집할 텍스트가 없습니다.")
        text = str(params.get("text") if params.get("text") is not None else "")
        if len(text) > MAX_PPT_TEXT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"PowerPoint 텍스트는 최대 {MAX_PPT_TEXT_CHARS:,}자까지 지원합니다."
            )
        noop = text == state["selected_text"]
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "requested_digest": adapter._digest(text),
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
                "text": text,
                "original_text": state["selected_text"],
                "selection_type": state["selection_type"],
                "text_start": state["text_start"],
                "text_length": state["text_length"],
            },
            current_state=adapter._common_current_state(state),
            estimated_changes=(
                0 if noop else max(len(text), len(state["selected_text"]))
            ),
            destructive=not noop,
            reversible=True,
            verification_method="read_powerpoint_shape_text",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, target, current):
        shape = target.shape
        text_range = adapter._range_for_prepared(shape, current)
        text = current.params["text"]
        original = current.params["original_text"]
        start = int(current.params["text_start"])
        whole_shape = current.params["selection_type"] == PPT_SELECTION_SHAPES
        try:
            text_range.Text = text
            full = shape.TextFrame.TextRange
            actual = (
                str(full.Text or "")
                if whole_shape
                else str(full.Characters(start, len(text)).Text or "")
            )
            if actual != text:
                raise AppActionVerificationError(
                    "PowerPoint Shape 텍스트 수정 결과가 요청과 다릅니다."
                )
            if not whole_shape:
                full.Characters(start, len(text)).Select()
        except Exception as error:
            try:
                full = shape.TextFrame.TextRange
                if whole_shape:
                    full.Text = original
                else:
                    full.Characters(start, len(text)).Text = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "PowerPoint 텍스트 수정 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"text_length": len(text)})


__all__ = ["ReplaceShapeTextOperation"]
