"""Turn the current 한글 paragraph into a bullet or numbered list.

한글's ParagraphShapeBullet / ParagraphShapeNumber actions do nothing when run,
and two other plausible action names opened a modal dialog that blocked
automation entirely.  ``HParaShape.HeadingType`` does work, so this goes
through the parameter set exactly like line spacing and alignment.
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
    HEADING_BULLET,
    HEADING_NONE,
    HEADING_NUMBER,
    paragraph_state,
)
from engine.vocabulary.list_format import (
    BULLET,
    NONE,
    NUMBER,
    list_format_label,
    normalize_list_format,
)

HEADING_TYPES = {
    NONE: HEADING_NONE,
    BULLET: HEADING_BULLET,
    NUMBER: HEADING_NUMBER,
}


class SetListFormatOperation(HwpOperation):
    name = "set_list_format"

    def prepare(self, adapter, hwp, params):
        _, base, _, selection = adapter._context(hwp)
        try:
            canonical = normalize_list_format(
                params.get("list_format")
                if params.get("list_format") is not None
                else params.get("style")
            )
        except ValueError as error:
            raise AppActionBlocked(str(error)) from error
        heading = HEADING_TYPES[canonical]
        current = paragraph_state(hwp)
        if "heading_type" not in current:
            raise AppActionBlocked(
                "현재 한글 문서에서 글머리표 상태를 읽지 못해 변경하지 않았습니다."
            )
        noop = current["heading_type"] == heading
        target = adapter._target(base, selection, paragraph=True)
        label = list_format_label(canonical)
        snapshot = {
            **base,
            "operation": self.name,
            "target": target,
            "paragraph_state": current,
            "heading_type": heading,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=target,
            params={
                "list_format": canonical,
                "heading_type": heading,
                "list_format_label": label,
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
            verification_method="read_paragraph_heading_type",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        before = current.current_state["format"]
        if current.noop:
            return adapter._result(current, before, before, False)
        heading = int(current.params["heading_type"])
        try:
            shape = hwp.HParameterSet.HParaShape
            hwp.HAction.GetDefault("ParagraphShape", shape.HSet)
            shape.HeadingType = heading
            hwp.HAction.Execute("ParagraphShape", shape.HSet)
            after = paragraph_state(hwp)
            if after.get("heading_type") != heading:
                raise AppActionVerificationError("한글 글머리표 적용 결과가 요청과 다릅니다.")
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 글머리표 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, True)


__all__ = ["HEADING_TYPES", "SetListFormatOperation"]
