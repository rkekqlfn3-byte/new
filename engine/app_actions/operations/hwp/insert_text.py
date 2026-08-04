"""Insert text at the Hanword cursor or over the current selection."""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import (
    MAX_FORMAT_SELECTION_CHARS,
    MAX_INSERT_TEXT_CHARS,
    HwpOperation,
)
from engine.app_actions.operations.hwp.state import char_state, paragraph_state


def normalize_insert_text(value):
    text = str(value if value is not None else "")
    if not text:
        raise AppActionBlocked("한글에 입력할 텍스트가 비어 있습니다.")
    if len(text) > MAX_INSERT_TEXT_CHARS or "\x00" in text:
        raise AppActionBlocked(
            f"한 번에 입력할 텍스트는 최대 {MAX_INSERT_TEXT_CHARS:,}자이며 NUL 문자를 포함할 수 없습니다."
        )
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


class InsertTextOperation(HwpOperation):
    name = "insert_text"

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        text = normalize_insert_text(params.get("text"))
        expected_occurrences = (
            document_text.count(text) - selection["text"].count(text) + 1
        )
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._target(base, selection),
            "text_digest_requested": adapter._digest_text(text),
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=snapshot["target"],
            params={
                "text": text,
                "expected_occurrences": expected_occurrences,
                "original_text": (
                    selection["text"]
                    if len(selection["text"]) <= MAX_FORMAT_SELECTION_CHARS
                    else None
                ),
                "selection_coordinates": list(selection["coordinates"]),
            },
            current_state={
                "position": base["position"],
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "selected_preview": selection["text"][:120],
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
            },
            estimated_changes=max(len(text), selection["text_length"]),
            destructive=selection["has_selection"],
            reversible=True,
            verification_method="read_document_text_and_position",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        _, before_base, before_text, _ = adapter._context(hwp)
        try:
            action = hwp.CreateAction("InsertText")
            parameter_set = action.CreateSet()
            parameter_set.SetItem("Text", current.params["text"])
            action.Execute(parameter_set)
            _, after_base, after_text, _ = adapter._context(hwp)
            post_format = {
                **char_state(hwp),
                "alignment": paragraph_state(hwp)["alignment"],
            }
            if (
                after_base["text_digest"] == before_base["text_digest"]
                or after_text.count(current.params["text"])
                != current.params["expected_occurrences"]
            ):
                raise AppActionVerificationError("한글 텍스트 입력 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 텍스트 입력 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"position": before_base["position"]},
            {
                "position": after_base["position"],
                "inserted_length": len(current.params["text"]),
                "document_digest": after_base["text_digest"],
                "format": post_format,
            },
            True,
        )


__all__ = ["InsertTextOperation", "normalize_insert_text"]
