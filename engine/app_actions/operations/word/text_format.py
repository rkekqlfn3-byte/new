"""Apply bold and font size to the Word selection."""

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
from engine.app_actions.operations.word.base import WordOperation
from engine.app_actions.operations.word.state import apply_text_format


class SetTextFormatOperation(WordOperation):
    name = "set_text_format"

    def desired_format(self, adapter, params, state):
        prepared_desired = params.get("desired")
        if isinstance(prepared_desired, dict):
            desired = {}
            if prepared_desired.get("bold") is not None:
                adapter._uniform_int(state["bold"], "굵기")
                desired["bold"] = -1 if int(prepared_desired["bold"]) != 0 else 0
            if prepared_desired.get("font_size") is not None:
                adapter._uniform_float(state["font_size"], "글자 크기")
                size = float(prepared_desired["font_size"])
                if not 1 <= size <= 1638:
                    raise AppActionBlocked("Word 글자 크기는 1~1638pt 사이여야 합니다.")
                desired["font_size"] = size
            if desired:
                return desired
        desired = {}
        if params.get("bold") is not None:
            adapter._uniform_int(state["bold"], "굵기")
            desired["bold"] = -1 if bool(params["bold"]) else 0
        if params.get("font_size") is not None:
            adapter._uniform_float(state["font_size"], "글자 크기")
            size = float(params["font_size"])
            if not 1 <= size <= 1638:
                raise AppActionBlocked("Word 글자 크기는 1~1638pt 사이여야 합니다.")
            desired["font_size"] = size
        elif params.get("font_size_delta") is not None:
            adapter._uniform_float(state["font_size"], "글자 크기")
            size = state["font_size"] + float(params["font_size_delta"])
            if not 1 <= size <= 1638:
                raise AppActionBlocked("변경할 Word 글자 크기가 안전 범위를 벗어납니다.")
            desired["font_size"] = size
        if not desired:
            raise AppActionBlocked("변경할 Word 글자 서식을 지정해주세요.")
        return desired

    def prepare(self, adapter, session, params):
        _, state = adapter._context(session.application, session.document)
        if not state["has_selection"]:
            raise AppActionBlocked("서식을 적용할 Word 텍스트를 먼저 선택해주세요.")
        desired = self.desired_format(adapter, params, state)
        noop = all(state[key] == value for key, value in desired.items())
        snapshot = {
            **adapter._base_snapshot(state, self.name),
            "current": {"bold": state["bold"], "font_size": state["font_size"]},
            "desired": desired,
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
                "desired": desired,
                "original": {
                    "bold": state["bold"],
                    "font_size": state["font_size"],
                },
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
            estimated_changes=0 if noop else state["selected_length"],
            destructive=not noop and state["selected_length"] > 1000,
            reversible=True,
            verification_method="read_word_font",
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=noop,
        )

    def run(self, adapter, session, current):
        document = session.document
        target = document.Range(current.params["start"], current.params["end"])
        desired = current.params["desired"]
        original = current.params["original"]
        try:
            apply_text_format(target, desired)
            if "bold" in desired and int(target.Font.Bold) != int(desired["bold"]):
                raise AppActionVerificationError("Word 굵기 적용 결과가 다릅니다.")
            if "font_size" in desired and abs(
                float(target.Font.Size) - float(desired["font_size"])
            ) > 0.01:
                raise AppActionVerificationError("Word 글자 크기 적용 결과가 다릅니다.")
        except Exception as error:
            try:
                apply_text_format(target, original)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Word 글자 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, True, {"format": desired})


__all__ = ["SetTextFormatOperation"]
