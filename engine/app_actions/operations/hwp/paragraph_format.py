"""Align the current Hanword paragraph or selection."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation
from engine.app_actions.operations.hwp.state import (
    PARAGRAPH_ALIGNMENTS,
    normalize_alignment,
    paragraph_state,
)


class SetParagraphFormatOperation(HwpOperation):
    name = "set_paragraph_format"

    def prepare(self, adapter, hwp, params):
        _, base, _, selection = adapter._context(hwp)
        alignment = normalize_alignment(params.get("alignment"))
        expected = PARAGRAPH_ALIGNMENTS[alignment][1]
        current = paragraph_state(hwp)
        noop = current["alignment"] == expected
        target = adapter._target(base, selection, paragraph=True)
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "paragraph_state": current,
            "alignment": alignment,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={"alignment": alignment},
            current_state={
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "format": current,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if noop else max(1, selection["text_length"]),
            destructive=selection["text_length"] > 5000 and not noop,
            reversible=True,
            verification_method="read_paragraph_shape",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["format"]
        if current.noop:
            return adapter._result(current, before, before, False)
        action_name, expected = PARAGRAPH_ALIGNMENTS[current.params["alignment"]]
        try:
            hwp.HAction.Run(action_name)
            after = paragraph_state(hwp)
            if after["alignment"] != expected:
                raise AppActionVerificationError("한글 문단 정렬 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 문단 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = ["SetParagraphFormatOperation"]
