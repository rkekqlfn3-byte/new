"""Start a new page at the 한글 cursor.

Verified by ``PageCount``, which a real 한글 install reports going from 1 to 2
when ``BreakPage`` runs — the page count is the change the user asked for,
where the document text is only incidentally longer.

한글 refuses the action outright in some positions, notably with the caret
inside a table, and signals that by returning False rather than by raising.
The return value is therefore load-bearing: without it the operation would
report a plain verification failure for something the user can simply fix by
moving the caret.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation


def page_count(hwp) -> int:
    try:
        return int(hwp.PageCount)
    except Exception as error:
        raise AppActionBlocked(
            "현재 한글 문서의 쪽 수를 읽지 못해 쪽을 나누지 않았습니다."
        ) from error


class InsertPageBreakOperation(HwpOperation):
    name = "insert_page_break"

    def prepare(self, adapter, hwp, params):
        _, base, document_text, selection = adapter._context(hwp)
        if selection["has_selection"]:
            raise AppActionBlocked(
                "선택 영역이 있으면 쪽을 나누지 않습니다. 커서만 둔 뒤 다시 요청해주세요."
            )
        pages = page_count(hwp)
        snapshot = {
            **base,
            "operation": self.name,
            "target": adapter._target(base, selection),
            "page_count": pages,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{pages + 1}쪽 시작",
            params={"expected_page_count": pages + 1},
            current_state={
                "page_count": pages,
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
                "position": base["position"],
            },
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_page_count",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["page_count"]
        expected = int(current.params["expected_page_count"])
        try:
            if not hwp.HAction.Run("BreakPage"):
                raise AppActionBlocked(
                    "한글이 현재 커서 위치에서 쪽 나눔을 허용하지 않았습니다. "
                    "표 바깥에 커서를 두고 다시 요청해주세요."
                )
            after = page_count(hwp)
            if after != expected:
                raise AppActionVerificationError(
                    "한글 쪽 나눔 결과가 예상과 다릅니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 쪽 나눔 또는 검증에 실패했습니다."
            ) from error
        _, after_base, _, _ = adapter._context(hwp)
        return adapter._result(
            current,
            {"page_count": before},
            {"page_count": after, "document_digest": after_base["text_digest"]},
            True,
        )


__all__ = ["InsertPageBreakOperation", "page_count"]
