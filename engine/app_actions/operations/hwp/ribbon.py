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
# Five names the catalogue accepted are absent. BreakLine and
# CharShapeSpacingIncrease return True and move the caret without altering
# the document, which the catalogue counted as a change until it was fixed.
# InsertDateCode and InsertCpNo insert a field whose text the document
# digest does not carry, so the operation cannot confirm they worked.
# DeleteWord deletes forward and does nothing with the caret at the end,
# which is where the catalogue left it.
# The names themselves are no longer guessed. ``verification/hwp_action_names``
# reads 한글's own action table out of HwpAppModule.dll, which is how
# InsertHyperlink turned up after HyperlinkInsert had been recorded as a
# missing feature for a whole run.
#
# Four more accepted by the catalogue are absent for the same kind of reason
# as the first two: InsertPageNum, InsertDateCode, InsertUserName and
# InsertFixedWidthSpace insert content the document digest does not carry,
# so the operation cannot confirm they did anything. InsertCpTpNo,
# InsertFileName and InsertFilePath insert visible text and do work.
#
# The whole 6368-name list was probed once: 97 changed, 84 undid cleanly,
# 183 opened a dialog. 84 is the ceiling for driving 한글 this way, which is
# far below the "hundreds" this approach looked like it promised.
#
# Also absent, and for reasons worth keeping: the six FormObjCreator entries
# need 한글's form design mode, which this operation does not enter, and
# InsertLastSaveDate and InsertLastPrintDate have nothing to insert into a
# document that has never been saved or printed. ManualChangeHangul reported
# success and changed nothing even with 大韓民國 selected, so 한자 conversion
# is not claimed until something is found that actually performs it.
#
# See verification/hwp_action_catalogue.json for the full run, including the
# names that failed and the ones that open a dialog.
RIBBON_ACTIONS: dict[str, RibbonAction] = {
    "italic": RibbonAction("CharShapeItalic", "기울임", True, words=("기울임", "이탤릭", "기울여",)),
    "underline": RibbonAction("CharShapeUnderline", "밑줄", True, words=("밑줄", "언더라인",)),
    "strikethrough": RibbonAction("CharShapeStrikeout", "취소선", True, words=("취소선", "가운뎃줄",)),
    "superscript": RibbonAction("CharShapeSuperscript", "위 첨자", True, words=("위 첨자", "위첨자", "윗첨자",)),
    "subscript": RibbonAction("CharShapeSubscript", "아래 첨자", True, words=("아래 첨자", "아래첨자", "밑첨자",)),
    "outline": RibbonAction("CharShapeOutline", "외곽선", True, words=("외곽선", "테두리 글자",)),
    "shadow": RibbonAction("CharShapeShadow", "그림자", True, words=("그림자",)),
    "column_break": RibbonAction("BreakColumn", "단 나누기", words=("단 나누", "단나누",)),
    "indent_more": RibbonAction(
        "ParagraphShapeIndentPositive", "들여쓰기",
        words=("들여쓰", "들여 쓰", "안으로 밀"),
    ),
    "indent_less": RibbonAction(
        "ParagraphShapeIndentNegative", "내어쓰기",
        words=("내어쓰", "내어 쓰", "밖으로 밀"),
    ),
    "line_spacing_wider": RibbonAction(
        "ParagraphShapeIncreaseLineSpacing", "줄 간격 넓히기",
        words=("줄간격 넓", "줄 간격 넓", "줄간격 늘", "줄 간격 늘"),
    ),
    "line_spacing_narrower": RibbonAction(
        "ParagraphShapeDecreaseLineSpacing", "줄 간격 좁히기",
        words=("줄간격 좁", "줄 간격 좁", "줄간격 줄", "줄 간격 줄"),
    ),
    "margin_wider": RibbonAction(
        "ParagraphShapeIncreaseMargin", "문단 여백 넓히기",
        words=("문단 여백 넓", "문단여백 넓", "문단 여백 늘"),
    ),
    "margin_narrower": RibbonAction(
        "ParagraphShapeDecreaseMargin", "문단 여백 좁히기",
        words=("문단 여백 좁", "문단여백 좁", "문단 여백 줄"),
    ),
    "align_division": RibbonAction(
        "ParagraphShapeAlignDivision", "나눔 정렬", words=("나눔 정렬",),
    ),
    "memo": RibbonAction(
        "InsertFieldMemo", "메모", words=("메모",),
    ),
    "page_of_total": RibbonAction(
        "InsertCpTpNo", "현재 쪽/전체 쪽 넣기",
        words=("전체 쪽", "몇 쪽 중", "쪽 분의"),
    ),
    "insert_file_name": RibbonAction(
        "InsertFileName", "파일 이름 넣기", words=("파일 이름 넣", "파일명 넣"),
    ),
    "insert_file_path": RibbonAction(
        "InsertFilePath", "파일 경로 넣기", words=("파일 경로 넣", "경로 넣"),
    ),
    "left_margin_wider": RibbonAction(
        "ParagraphShapeIncreaseLeftMargin", "왼쪽 여백 넓히기",
        words=("왼쪽 여백 넓", "왼쪽 여백 늘", "좌측 여백 넓"),
    ),
    "left_margin_narrower": RibbonAction(
        "ParagraphShapeDecreaseLeftMargin", "왼쪽 여백 좁히기",
        words=("왼쪽 여백 좁", "왼쪽 여백 줄", "좌측 여백 좁"),
    ),
    "right_margin_wider": RibbonAction(
        "ParagraphShapeIncreaseRightMargin", "오른쪽 여백 넓히기",
        words=("오른쪽 여백 넓", "오른쪽 여백 늘", "우측 여백 넓"),
    ),
    "right_margin_narrower": RibbonAction(
        "ParagraphShapeDecreaseRightMargin", "오른쪽 여백 좁히기",
        words=("오른쪽 여백 좁", "오른쪽 여백 줄", "우측 여백 좁"),
    ),
    "paste": RibbonAction(
        "Paste", "붙여넣기", words=("붙여넣", "붙여 넣", "붙이기"),
    ),
    "paste_without_field": RibbonAction(
        "PasteExceptField", "필드 빼고 붙여넣기", words=("필드 빼고", "필드 없이 붙여"),
    ),
    "paste_page": RibbonAction(
        "PastePage", "쪽 붙여넣기", words=("쪽 붙여넣", "쪽 붙여 넣"),
    ),
    "insert_doc_info": RibbonAction(
        "InsertDocInfo", "문서 정보 넣기", words=("문서 정보 넣", "문서정보 넣"),
    ),
    "insert_last_save_by": RibbonAction(
        "InsertLastSaveBy", "마지막 저장자 넣기", words=("마지막 저장자", "최종 저장자"),
    ),
    "insert_datetime_field": RibbonAction(
        "InsertFieldDateTime", "날짜·시간 필드 넣기", words=("날짜·시간 필드", "날짜 시간 필드", "날짜시간 필드"),
    ),
    "insert_datetime_text": RibbonAction(
        "InsertStringDateTime", "날짜·시간 글자 넣기", words=("날짜·시간 글자", "날짜 시간 넣", "지금 시각 넣"),
    ),
    "list_level_down": RibbonAction(
        "ParaNumberBulletLevelDown", "번호 수준 내리기", words=("수준 내리", "수준 내려", "수준 낮춰", "한 단계 내려"),
    ),
    "paragraph_break": RibbonAction(
        "BreakPara", "문단 나누기", words=("문단 나누", "문단나누"),
    ),
    "insert_tab": RibbonAction(
        "InsertTab", "탭 넣기", words=("탭 넣", "탭 삽입"),
    ),
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
PARA_FIELDS = (
    "AlignType", "HeadingType", "LineSpacing", "Indentation",
    "LeftMargin", "RightMargin",
)


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
