"""Run a 한글 ribbon command that has no operation of its own.

한글 exposes its ribbon through ``HAction.Run("<name>")``, so a command like
각주 needs a verified name rather than an implementation.  The names here
are not guessed — guessing is what opened a modal dialog that had to be
killed while table support was being written.  Each one was run against a
real 한글 by ``verification/hwp_action_catalogue.py``, which recorded that
it changed the document and that a single Undo put the document back.  Names
that did nothing, returned False, or opened a dialog are not in this table.

Commands that already have an operation are deliberately absent.  두 routes
to the same edit is how ``글머리표 없애줘`` ended up offering to delete the
reader's table: whichever matched first won.  A test keeps this table and
the rest of the registry disjoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation


@dataclass(frozen=True, slots=True)
class RibbonAction:
    action: str
    label: str
    needs_selection: bool = False
    # How people ask for it. Kept beside the action so a new ribbon command
    # cannot be added without saying what someone would call it.
    words: tuple[str, ...] = ()


# Verified 2026-08-05 against a real 한글: changed the document and undid
# cleanly, and then run again through this operation against a real 한글.
# Two names the catalogue accepted are absent: BreakLine and
# CharShapeSpacingIncrease return True and move the caret without altering
# the document, which the catalogue counted as a change until it was fixed.
# See verification/hwp_action_catalogue.json for the full run, including the
# names that failed and the eight that open a dialog.
RIBBON_ACTIONS: dict[str, RibbonAction] = {
    "italic": RibbonAction("CharShapeItalic", "기울임", True, words=("기울임", "이탤릭", "기울여",)),
    "underline": RibbonAction("CharShapeUnderline", "밑줄", True, words=("밑줄", "언더라인",)),
    "strikethrough": RibbonAction("CharShapeStrikeout", "취소선", True, words=("취소선", "가운뎃줄",)),
    "superscript": RibbonAction("CharShapeSuperscript", "위 첨자", True, words=("위 첨자", "위첨자", "윗첨자",)),
    "subscript": RibbonAction("CharShapeSubscript", "아래 첨자", True, words=("아래 첨자", "아래첨자", "밑첨자",)),
    "outline": RibbonAction("CharShapeOutline", "외곽선", True, words=("외곽선", "테두리 글자",)),
    "shadow": RibbonAction("CharShapeShadow", "그림자", True, words=("그림자",)),
    "column_break": RibbonAction("BreakColumn", "단 나누기", words=("단 나누", "단나누",)),
    "footnote": RibbonAction("InsertFootnote", "각주", words=("각주",)),
    "endnote": RibbonAction("InsertEndnote", "미주", words=("미주",)),
}


# ``char_state`` carries only what the operations that use it set — bold,
# size, colour — so italic and underline are invisible to it. Reading the
# narrow one here reported every one of these as having done nothing, which
# is the same mistake the first catalogue run made by reading only the text.
CHAR_FIELDS = (
    "Bold", "Italic", "UnderlineType", "StrikeOutType", "SuperScript",
    "SubScript", "Height", "TextColor", "Spacing", "OutLineType",
    "ShadowType",
)
PARA_FIELDS = ("AlignType", "HeadingType", "LineSpacing", "Indentation")


def _shape_state(hwp, action_name: str, set_name: str, fields) -> dict:
    try:
        shape = getattr(hwp.HParameterSet, set_name)
        hwp.HAction.GetDefault(action_name, shape.HSet)
    except Exception:
        return {}
    state = {}
    for field in fields:
        try:
            state[field] = int(getattr(shape, field))
        except Exception:
            continue
    return state


def ribbon_state(hwp) -> dict:
    """What has to differ for the command to have done something."""
    return {
        "char": _shape_state(hwp, "CharShape", "HCharShape", CHAR_FIELDS),
        "paragraph": _shape_state(
            hwp, "ParagraphShape", "HParaShape", PARA_FIELDS
        ),
    }


class RunRibbonActionOperation(HwpOperation):
    name = "run_ribbon_action"

    def prepare(self, adapter, hwp, params):
        key = str(params.get("ribbon_action") or "").strip().casefold()
        entry = RIBBON_ACTIONS.get(key)
        if entry is None:
            raise AppActionBlocked(
                "확인되지 않은 한글 기능입니다. 실제 한글에서 동작을 확인한 "
                "기능만 실행합니다."
            )
        _, base, document_text, selection = adapter._context(hwp)
        if entry.needs_selection and not selection["has_selection"]:
            raise AppActionBlocked(
                f"{entry.label}은(는) 적용할 글을 먼저 선택해주세요."
            )
        state = ribbon_state(hwp)
        snapshot = {
            **base,
            "operation": self.name,
            "ribbon_action": key,
            "target": adapter._target(base, selection),
            "state": state,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=f"{adapter._target(base, selection)} · {entry.label}",
            params={"ribbon_action": key, "action_name": entry.action},
            current_state={
                "state": state,
                "document_length": len(document_text),
                "document_digest": base["text_digest"],
                "position": base["position"],
            },
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_shape_state",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        key = str(current.params["ribbon_action"])
        entry = RIBBON_ACTIONS[key]
        before = current.current_state
        try:
            if not hwp.HAction.Run(entry.action):
                raise AppActionBlocked(
                    f"한글이 현재 위치에서 {entry.label}을(를) 허용하지 "
                    "않았습니다. 커서나 선택을 옮기고 다시 요청해주세요."
                )
            _, after_base, after_text, _ = adapter._context(hwp)
            after = {
                "state": ribbon_state(hwp),
                "document_length": len(after_text),
                "document_digest": after_base["text_digest"],
            }
            unchanged = (
                after["state"] == before["state"]
                and after["document_digest"] == before["document_digest"]
            )
            if unchanged:
                raise AppActionVerificationError(
                    f"한글에서 {entry.label}이(가) 적용되지 않았습니다."
                )
        except Exception as error:
            try:
                adapter._undo(hwp)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                f"한글 {entry.label} 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(
            current,
            {"state": before["state"], "document_digest": before["document_digest"]},
            {"state": after["state"], "document_digest": after["document_digest"]},
            True,
        )


__all__ = ["RIBBON_ACTIONS", "RibbonAction", "RunRibbonActionOperation", "ribbon_state"]
