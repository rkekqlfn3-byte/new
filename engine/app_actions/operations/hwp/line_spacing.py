"""Set the line spacing of the current 한글 paragraph or selection.

Korean office templates prescribe 줄간격 (160% is the common public-sector
default), so this is one of the more frequently needed paragraph settings.
"""

from __future__ import annotations

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation
from engine.app_actions.operations.hwp.state import (
    LINE_SPACING_PERCENT_TYPE,
    paragraph_state,
)
from engine.vocabulary.line_spacing import (
    line_spacing_label,
    normalize_line_spacing,
)


class SetLineSpacingOperation(HwpOperation):
    name = "set_line_spacing"

    def prepare(self, adapter, hwp, params):
        _, base, _, selection = adapter._context(hwp)
        try:
            percent = normalize_line_spacing(
                params.get("line_spacing")
                if params.get("line_spacing") is not None
                else params.get("percent")
            )
        except ValueError as error:
            raise AppActionBlocked(str(error)) from error
        current = paragraph_state(hwp)
        if "line_spacing" not in current:
            raise AppActionBlocked(
                "현재 한글 문서에서 줄간격을 읽지 못해 변경하지 않았습니다."
            )
        noop = (
            current["line_spacing"] == percent
            and current["line_spacing_type"] == LINE_SPACING_PERCENT_TYPE
        )
        target = adapter._target(base, selection, paragraph=True)
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "paragraph_state": current,
            "line_spacing": percent,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "line_spacing": percent,
                "line_spacing_type": LINE_SPACING_PERCENT_TYPE,
                "line_spacing_label": line_spacing_label(percent),
            },
            current_state={
                "has_selection": selection["has_selection"],
                "selected_length": selection["text_length"],
                "selected_digest": selection["text_digest"],
                "format": current,
                "document_digest": base["text_digest"],
            },
            estimated_changes=0 if noop else max(1, selection["text_length"]),
            destructive=False,
            reversible=True,
            verification_method="read_paragraph_line_spacing",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["format"]
        if current.noop:
            return adapter._result(current, before, before, False)
        percent = int(current.params["line_spacing"])
        try:
            shape = hwp.HParameterSet.HParaShape
            hwp.HAction.GetDefault("ParagraphShape", shape.HSet)
            shape.LineSpacingType = LINE_SPACING_PERCENT_TYPE
            shape.LineSpacing = percent
            hwp.HAction.Execute("ParagraphShape", shape.HSet)
            after = paragraph_state(hwp)
            if (
                after.get("line_spacing") != percent
                or after.get("line_spacing_type") != LINE_SPACING_PERCENT_TYPE
            ):
                raise AppActionVerificationError("한글 줄간격 적용 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 줄간격 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = ["SetLineSpacingOperation"]
