"""Align the current Word paragraph or selection."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.app_actions.operations.word.base import WordOperation
from engine.app_actions.operations.word.state import normalize_alignment


class SetParagraphFormatOperation(WordOperation):
    name = "set_paragraph_format"

    def prepare(self, adapter, session, params):
        _, state = adapter._context(session.application, session.document)
        adapter._uniform_int(state["alignment"], "문단 정렬")
        label, alignment = normalize_alignment(params.get("alignment"))
        noop = state["alignment"] == alignment
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "current_alignment": state["alignment"],
            "desired_alignment": alignment,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=adapter._target(state),
            params={
                "document_path": state["document_id"],
                "start": state["start"],
                "end": state["end"],
                "alignment": alignment,
                "alignment_label": label,
                "original_alignment": state["alignment"],
            },
            current_state={
                "has_selection": state["has_selection"],
                "selected_length": state["selected_length"],
                "selected_digest": state["selected_digest"],
                "selected_preview": state["selected_text"][:120],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            },
            estimated_changes=0 if noop else max(1, state["selected_length"]),
            destructive=False,
            reversible=True,
            verification_method="read_word_paragraph_alignment",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, session, current):
        document = session.document
        target = document.Range(current.params["start"], current.params["end"])
        alignment = int(current.params["alignment"])
        original = int(current.params["original_alignment"])
        try:
            target.ParagraphFormat.Alignment = alignment
            if int(target.ParagraphFormat.Alignment) != alignment:
                raise AppActionVerificationError("Word 문단 정렬 결과가 다릅니다.")
        except Exception as error:
            try:
                target.ParagraphFormat.Alignment = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 문단 정렬 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"alignment": alignment})


__all__ = ["SetParagraphFormatOperation"]
