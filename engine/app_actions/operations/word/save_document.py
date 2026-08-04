"""Save the connected Word document in place."""

from __future__ import annotations

import os

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


class SaveDocumentOperation(WordOperation):
    name = "save_document"

    def prepare(self, adapter, session, params):
        _, state = adapter._context(session.application, session.document)
        stat = os.stat(state["document_id"])
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "file_size": int(stat.st_size),
            "file_mtime_ns": int(stat.st_mtime_ns),
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=state["document_id"],
            workbook_name=state["document_name"],
            sheet="현재 문서",
            target=state["document_name"],
            params={"document_path": state["document_id"]},
            current_state={
                "saved": state["saved"],
                "document_length": state["document_length"],
            },
            estimated_changes=0 if state["saved"] else 1,
            destructive=not state["saved"],
            reversible=False,
            verification_method="verify_word_saved_and_file_exists",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=state["saved"],
        )

    def run(self, adapter, session, current):
        document = session.document
        try:
            document.Save()
            if not bool(document.Saved) or not os.path.isfile(current.document_id):
                raise AppActionVerificationError("Word 문서 저장 결과를 확인하지 못했습니다.")
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionVerificationError("Word 문서 저장에 실패했습니다.") from error
        return adapter._result(current, True, {"saved": True})


__all__ = ["SaveDocumentOperation"]
