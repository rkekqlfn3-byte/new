"""Delete the current 한글 selection.

Hanword reports a selection as list/paragraph/position triplets rather than
plain character offsets, so the result cannot be verified by rebuilding the
expected string.  It is verified by three facts that together pin the change:
the document digest moved, the length dropped by exactly the selected length,
and the selected text occurs exactly one time fewer than before.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import (
    MAX_FORMAT_SELECTION_CHARS,
    HwpOperation,
)


class DeleteTextOperation(HwpOperation):
    name = "delete_text"

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        if not selection["has_selection"]:
            raise AppActionBlocked("삭제할 텍스트를 한글에서 먼저 선택해주세요.")
        if not selection["text"]:
            raise AppActionBlocked("선택 영역에서 삭제할 내용을 읽지 못했습니다.")
        if selection["text_length"] > MAX_FORMAT_SELECTION_CHARS:
            raise AppActionBlocked(
                f"한 번에 삭제할 수 있는 텍스트는 최대 {MAX_FORMAT_SELECTION_CHARS:,}자입니다."
            )
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._target(base, selection),
            "selected_digest": selection["text_digest"],
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=snapshot["target"],
            params={
                "original_text": selection["text"],
                "deleted_length": selection["text_length"],
                "expected_document_length": (
                    len(document_text) - selection["text_length"]
                ),
                "expected_occurrences": (
                    document_text.count(selection["text"]) - 1
                ),
                "selection_coordinates": list(selection["coordinates"]),
            },
            current_state={
                "has_selection": True,
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "selected_preview": selection["text"][:120],
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
            },
            estimated_changes=selection["text_length"],
            destructive=True,
            reversible=True,
            verification_method="read_document_text_after_delete",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        _, before_base, before_text, _ = adapter._context(hwp)
        original = current.params["original_text"]
        try:
            hwp.HAction.Run("Delete")
            _, after_base, after_text, _ = adapter._context(hwp)
            if after_base["text_digest"] == before_base["text_digest"]:
                raise AppActionVerificationError("한글 선택 영역이 삭제되지 않았습니다.")
            if len(after_text) != current.params["expected_document_length"]:
                raise AppActionVerificationError(
                    "한글 삭제 후 문서 길이가 예상과 다릅니다."
                )
            if after_text.count(original) != current.params["expected_occurrences"]:
                raise AppActionVerificationError(
                    "한글 삭제 결과가 선택한 내용과 다릅니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 텍스트 삭제 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {
                "document_length": len(before_text),
                "document_digest": before_base["text_digest"],
            },
            {
                "document_length": len(after_text),
                "document_digest": after_base["text_digest"],
                "deleted_length": current.params["deleted_length"],
            },
            True,
        )


__all__ = ["DeleteTextOperation"]
