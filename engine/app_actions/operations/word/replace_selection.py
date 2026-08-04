"""Replace the current Word selection with new text."""

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
from engine.app_actions.operations.word.base import (
    MAX_WORD_REPLACEMENT_CHARS,
    WordOperation,
)


class ReplaceSelectionOperation(WordOperation):
    name = "replace_selection"

    def prepare(self, adapter, session, params):
        _, state = adapter._context(session.application, session.document)
        if not state["has_selection"]:
            raise AppActionBlocked("교체할 Word 텍스트를 먼저 선택해주세요.")
        text = str(params.get("text") if params.get("text") is not None else "")
        if not text or len(text) > MAX_WORD_REPLACEMENT_CHARS or "\x00" in text:
            raise AppActionBlocked(
                f"Word 교체 텍스트는 1~{MAX_WORD_REPLACEMENT_CHARS:,}자여야 합니다."
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
            sheet="현재 문서",
            target=adapter._target(state),
            params={
                "document_path": state["document_id"],
                "text": text,
                "original_text": state["selected_text"],
                "start": state["start"],
                "end": state["end"],
            },
            current_state={
                "has_selection": True,
                "selected_length": state["selected_length"],
                "selected_digest": state["selected_digest"],
                "selected_preview": state["selected_text"][:120],
                "style_name": state["style_name"],
                "in_table": state["in_table"],
                "table": state["table"],
            },
            estimated_changes=0 if noop else max(len(text), state["selected_length"]),
            destructive=not noop,
            reversible=True,
            verification_method="read_word_range_text",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, session, current):
        document = session.document
        start = int(current.params["start"])
        end = int(current.params["end"])
        text = current.params["text"]
        original = current.params["original_text"]
        target = document.Range(start, end)
        try:
            target.Text = text
            actual = str(document.Range(start, start + len(text)).Text or "")
            if actual != text:
                raise AppActionVerificationError(
                    "Word 선택 텍스트 교체 결과가 요청과 다릅니다."
                )
            verified_range = document.Range(start, start + len(text))
            verified_range.Select()
            post_format = {
                "bold": int(verified_range.Font.Bold),
                "font_size": float(verified_range.Font.Size),
                "alignment": int(verified_range.ParagraphFormat.Alignment),
            }
        except Exception as error:
            try:
                document.Range(start, start + len(text)).Text = original
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 선택 텍스트 교체 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            True,
            {
                "text_length": len(text),
                "document_digest": adapter._digest(document.Content.Text),
                "format": post_format,
            },
        )


__all__ = ["ReplaceSelectionOperation"]
