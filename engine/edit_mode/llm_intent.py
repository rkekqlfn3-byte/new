"""Translate an edit command the rules did not recognise.

The rule analyser matches wordings someone wrote down in advance, so a
sentence one word away from a known one fails even when the operation it
asks for already exists.  ``번호 매겨줘`` is the plain way to ask for a
numbered list and 한글 numbering works, but no rule matched it.

Measured against the configured provider before this was written: sixteen
of sixteen unrecognised 한글 wordings — 행간, 라인 간격, 진하게, 두껍게,
센터 정렬, 가운데로 맞춰줘, 테이블 3행 4열, 표 3x4, 번호 매겨줘 among them
— came back as the right operation with the right parameters, and the three
asking for things 한글 support does not cover (쪽 번호, 맞춤법, 각주) came
back as unsupported rather than an invented operation name.

The model never touches the document.  It picks one name from a closed list
and fills that name's parameters; everything after it — preview, approval,
context fingerprint comparison, execution, verification, undo — is
unchanged.  A wrong guess is something the reader declines on the preview,
not an edit that already happened.

Every parameter name, value vocabulary and unit written into the guides
below was read out of what the rule analyser itself produces for a wording
it does recognise, not guessed: 한글 line spacing is a percent integer, not
1.5; Excel alignment allows no ``justify``; Excel colours are a fixed set of
six names.  A guide that disagreed with the operation would translate a
sentence into something the adapter then refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from engine.edit_mode.stage5 import EditIntent, Stage5EditError
from engine.llm.json_reply import json_reply
from engine.llm.provider_caller import provider_caller


def _named(table) -> str:
    """`key(label)` for every entry, so a guide cannot drift from its table.

    These lists used to be typed out by hand beside the tables they describe,
    and the Excel one had already drifted: it said 자동 줄바꿈 and 바깥 테두리
    where the table, corrected from Excel's own GetLabelMso, says 자동 줄 바꿈
    and 바깥쪽 테두리.
    """
    return ", ".join(f"{key}({entry.label})" for key, entry in table.items())


def _dialog_names() -> str:
    from engine.app_actions.operations.hwp import DIALOG_ACTIONS

    return ", ".join(sorted(DIALOG_ACTIONS))


def _hwp_ribbon_names() -> str:
    from engine.app_actions.operations.hwp import RIBBON_ACTIONS

    return _named(RIBBON_ACTIONS)


def _excel_ribbon_names() -> str:
    from engine.app_actions.operations.excel import EXCEL_COMMANDS

    return _named(EXCEL_COMMANDS)


_DIALOG_NAMES = _dialog_names()
_HWP_RIBBON_NAMES = _hwp_ribbon_names()
_EXCEL_RIBBON_NAMES = _excel_ribbon_names()

# Only parameters a person's sentence can actually specify.  The operations
# also read verification fields such as ``expected_cell_count``, which they
# compute while preparing; a supplied one would be describing state rather
# than asking for a change.
HWP_OPERATION_GUIDE: dict[str, tuple[str, tuple[str, ...]]] = {
    "set_text_format": (
        "글자 모양. bold=true/false, font_size=숫자(pt), "
        "font_size_delta=상대 증감(pt, '조금 크게'=2), text_color='빨강' 등",
        ("bold", "font_size", "font_size_delta", "text_color"),
    ),
    "set_paragraph_format": (
        "문단 정렬. alignment 는 left, center, right, justify 중 하나",
        ("alignment",),
    ),
    "set_line_spacing": (
        "줄 간격. line_spacing 은 퍼센트 정수 — 1.5배는 150, 2배는 200",
        ("line_spacing",),
    ),
    "set_list_format": (
        "글머리표/번호 목록. list_format 은 bullet, number, none 중 하나 "
        "(없애달라는 말이면 none)",
        ("list_format",),
    ),
    "insert_text": ("커서 위치에 글자 넣기. text=넣을 글자", ("text",)),
    "delete_text": ("선택한 글자 지우기. 파라미터 없음", ()),
    "find_replace": (
        "찾아 바꾸기. find=찾을 말, replace=바꿀 말, "
        "scope 는 selection 또는 document, match_case=true/false",
        ("find", "replace", "scope", "match_case"),
    ),
    "insert_page_break": ("쪽 나누기. 파라미터 없음", ()),
    "set_page_setup": (
        "쪽 여백과 용지 방향. margin_mm=여백(mm), "
        "margin_sides=['left','right','top','bottom'] 중 해당하는 것, "
        "orientation 은 portrait 또는 landscape",
        ("margin_mm", "margin_sides", "orientation"),
    ),
    "insert_table": ("표 만들기. rows=줄 수, columns=칸 수", ("rows", "columns")),
    "set_table_cell": (
        "표 한 칸에 글자 넣기. row=행 번호, column=열 번호, text=넣을 글자",
        ("row", "column", "text"),
    ),
    "insert_table_row": ("표에 줄 추가. row, column=기준 칸", ("row", "column")),
    "insert_table_column": ("표에 칸 추가. row, column=기준 칸", ("row", "column")),
    "delete_table_row": ("표의 줄 삭제. row, column=기준 칸", ("row", "column")),
    "delete_table_column": ("표의 칸 삭제. row, column=기준 칸", ("row", "column")),
    "merge_table_cells": ("선택한 표 칸 합치기. row, column=기준 칸", ("row", "column")),
    "split_table_cell": (
        "표 칸 나누기. row, column=기준 칸, rows/columns=나눌 개수",
        ("row", "column", "rows", "columns"),
    ),
    "set_table_column_width": (
        "표 칸 너비. column=열 번호, width_mm=너비(mm)",
        ("column", "width_mm", "row"),
    ),
    "set_table_border": (
        "표 테두리. row, column=기준 칸, color=색, thickness=굵기",
        ("row", "column", "color", "thickness"),
    ),
    "delete_table": ("표 통째로 삭제. 파라미터 없음", ()),
    "save_as": (
        "다른 이름으로 저장. path=저장할 전체 경로, format=파일 형식",
        ("path", "format"),
    ),
    "insert_hyperlink": (
        "하이퍼링크 넣기. text=링크에 보일 글, url=연결할 주소",
        ("text", "url"),
    ),
    "insert_bookmark": ("책갈피 넣기. name=책갈피 이름", ("name",)),
    "insert_page_number": ("쪽 번호 넣기. 파라미터 없음", ()),
    "insert_header": ("머리말 넣기. 파라미터 없음", ()),
    "set_font_name": ("글꼴 바꾸기. font=글꼴 이름 (예: 궁서, 맑은 고딕)", ("font",)),
    "convert_hanja_to_hangul": ("선택한 한자를 한글로 바꾸기. 파라미터 없음", ()),
    "open_hwp_dialog": (
        "자비스가 대신 해줄 수 없어 한글 설정 창만 열어주는 기능. "
        "실행이 되는 다른 작업이 있으면 반드시 그것을 먼저 쓴다. "
        "창은 스스로 한국어 이름을 달고 뜨므로, 요청과 뜻이 맞는 이름을 "
        "고르면 된다. dialog_action 은 다음 중 하나: " + _DIALOG_NAMES,
        ("dialog_action",),
    ),
    "run_ribbon_action": (
        "리본 기능. ribbon_action 은 다음 중 하나: " + _HWP_RIBBON_NAMES,
        ("ribbon_action",),
    ),
}

EXCEL_OPERATION_GUIDE: dict[str, tuple[str, tuple[str, ...]]] = {
    "write_cell": (
        "셀에 값 넣기. cell='A6', value=넣을 값, "
        "value_type 은 text, number, formula 중 하나",
        ("cell", "value", "value_type"),
    ),
    "sum_column_to_cell": (
        "합계. source_range=더할 범위, target_cell=결과 셀, "
        "result_mode 는 formula 또는 value",
        ("source_range", "target_cell", "result_mode"),
    ),
    "format_range": (
        "범위 서식. range=대상 범위, bold=true/false, font_size=숫자(pt), "
        "font_size_delta=상대 증감(pt), alignment 는 left, center, right 중 하나 "
        "(엑셀은 justify 없음), fill_color/font_color 는 "
        "red, orange, yellow, green, blue, gray 중 하나",
        (
            "range", "bold", "font_size", "font_size_delta",
            "alignment", "fill_color", "font_color",
        ),
    ),
    "find_replace": (
        "찾아 바꾸기. find, replace, scope 는 range 또는 sheet, range=대상 범위",
        ("find", "replace", "scope", "range"),
    ),
    "sort_range": (
        "정렬. table_range=대상 범위, column_name=기준 열, "
        "direction 은 ascending 또는 descending",
        ("table_range", "column_name", "direction"),
    ),
    "filter_range": (
        "필터. table_range=대상 범위, column_name=열, value=값, "
        "operator 는 eq, ne, gt, ge, lt, le 중 하나. "
        "해제는 clear=true 만 보낸다",
        ("table_range", "column_name", "operator", "value", "clear"),
    ),
    "insert_rows": (
        "행 추가. selection_range=기준 범위, count=개수",
        ("selection_range", "count"),
    ),
    "insert_columns": (
        "열 추가. selection_range=기준 범위, count=개수",
        ("selection_range", "count"),
    ),
    "apply_conditional_format": (
        "조건부 서식. range=대상 범위, operator 는 eq, ne, gt, ge, lt, le, "
        "threshold=기준 값, color 는 red, orange, yellow, green, blue, gray",
        ("range", "operator", "threshold", "color"),
    ),
    "run_excel_command": (
        "리본 기능. excel_command 는 다음 중 하나: " + _EXCEL_RIBBON_NAMES,
        ("excel_command",),
    ),
    "format_matching_values": (
        "조건에 맞는 셀만 칠하기. 파라미터는 apply_conditional_format 과 같다",
        ("range", "operator", "threshold", "color"),
    ),
}

WORD_OPERATION_GUIDE: dict[str, tuple[str, tuple[str, ...]]] = {
    "set_text_format": (
        "글자 모양. bold=true/false, font_size=숫자(pt), "
        "font_size_delta=상대 증감(pt, '조금 크게'=2)",
        ("bold", "font_size", "font_size_delta"),
    ),
    "set_paragraph_format": (
        "문단 정렬. alignment 는 left, center, right, justify 중 하나",
        ("alignment",),
    ),
    "replace_selection": ("선택한 글을 바꾸기. text=새 글", ("text",)),
    "save_document": ("문서 저장. 파라미터 없음", ()),
}

POWERPOINT_OPERATION_GUIDE: dict[str, tuple[str, tuple[str, ...]]] = {
    "set_text_format": (
        "글자 모양. bold=true/false, font_size=숫자(pt), "
        "font_size_delta=상대 증감(pt)",
        ("bold", "font_size", "font_size_delta"),
    ),
    "set_text_alignment": (
        "글 정렬. alignment 는 left, center, right 중 하나",
        ("alignment",),
    ),
    "replace_shape_text": ("도형 안 글 바꾸기. text=새 글", ("text",)),
    "move_shape": (
        "도형 옮기기. dx=가로 이동(pt, 오른쪽이 양수), "
        "dy=세로 이동(pt, 아래가 양수)",
        ("dx", "dy"),
    ),
    "resize_shape": (
        "도형 크기. scale=배율(1.1 이면 10% 크게)", ("scale",)
    ),
    "match_previous_style": ("앞 도형과 같은 서식으로. 파라미터 없음", ()),
}

# Only the applications whose rule output was read back and checked against
# the operations they feed.  An application missing here keeps the rules
# alone rather than being translated on a guess.
OPERATION_GUIDES: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "hwp": HWP_OPERATION_GUIDE,
    "excel": EXCEL_OPERATION_GUIDE,
    "word": WORD_OPERATION_GUIDE,
    "powerpoint": POWERPOINT_OPERATION_GUIDE,
}

APP_LABELS = {
    "hwp": "한글(HWP)",
    "excel": "Excel",
    "word": "Word",
    "powerpoint": "PowerPoint",
}

UNSUPPORTED = "unsupported"



def build_prompt(operations, app_type: str = "hwp") -> str:
    """The instruction the model answers, listing only what it may choose."""
    guide = OPERATION_GUIDES.get(app_type, {})
    allowed = sorted(set(operations) & set(guide))
    lines = [f"- {name}: {guide[name][0]}" for name in allowed]
    label = APP_LABELS.get(app_type, app_type)
    return (
        f"너는 {label} 문서 편집 명령을 아래 작업 중 하나로 번역한다.\n"
        "목록에 없는 기능을 요청하면 반드시 "
        f'"{UNSUPPORTED}" 를 쓴다. 작업 이름을 지어내지 마라.\n\n'
        "작업 목록:\n" + "\n".join(lines) + "\n\n"
        '출력은 JSON 한 줄만: {"operation": "...", "params": {...}, '
        '"description": "사용자에게 보여줄 한 줄 설명"}\n'
        "params 에는 위에 적힌 이름만 쓴다. 설명은 붙이지 마라."
    )


def describe_selection(context: Mapping[str, Any] | None) -> str:
    """What the reader has selected, so range parameters are not invented."""
    context = context or {}
    reference = str(context.get("selection_reference") or "").strip()
    preview = str(context.get("selected_text_preview") or "").strip()
    parts = []
    if reference:
        parts.append(f"현재 선택 범위: {reference}")
    if preview:
        parts.append(f"선택 내용: {preview[:80]}")
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class Translation:
    operation: str
    params: dict[str, Any]
    description: str




class LlmEditIntentTranslator:
    """Turn one unrecognised sentence into an allowed operation, or nothing.

    Every failure — no reply, unparseable reply, an operation outside the
    list, a parameter the operation does not take — returns ``None`` so the
    caller falls back to the message it would have shown anyway.  A guess
    that cannot be checked is worse than admitting the sentence was not
    understood.
    """

    def __init__(self, caller: Callable[[str, str], Any]):
        self._caller = caller

    def translate(
        self, text: str, operations, app_type: str = "hwp", context=None
    ) -> Translation | None:
        guide = OPERATION_GUIDES.get(app_type)
        if not guide:
            return None
        allowed = set(operations) & set(guide)
        if not allowed or not str(text or "").strip():
            return None
        selection = describe_selection(context)
        message = f"{selection}\n명령: {text}" if selection else str(text)
        try:
            raw = self._caller(build_prompt(allowed, app_type), message)
        except Exception:
            # An offline machine or a provider outage must not turn an
            # unrecognised sentence into a crash.
            return None
        payload = json_reply(raw)
        operation = str(payload.get("operation") or "").strip()
        if operation not in allowed:
            return None
        params = payload.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        if not set(params) <= set(guide[operation][1]):
            return None
        description = str(payload.get("description") or "").strip()
        return Translation(operation, params, description or text)


class LlmAssistedEditIntentAnalyzer:
    """Rules first; the model only sees sentences the rules turned down.

    Keeping the rules in front means the common wordings stay instant, free
    and available with no network, and the provider is asked only about the
    sentences that would otherwise have failed outright.
    """

    def __init__(
        self,
        structured,
        translator: LlmEditIntentTranslator | None,
        supported_operations=None,
        memory=None,
        composer=None,
    ):
        self._structured = structured
        self._translator = translator
        self._memory = memory
        self._composer = composer
        # The adapter refuses an operation outside its own allow-list after
        # analysis, so translating into one would only trade a clear message
        # for a confusing one. Ask for the same list up front.
        self._supported = frozenset(
            supported_operations
            if supported_operations is not None
            else set().union(*OPERATION_GUIDES.values())
        )

    # The most recent layer 3 proposal, or None. Read by the caller that
    # shows the refusal, so the reader sees what could be done instead.
    last_proposal = None

    @property
    def memory(self):
        return self._memory

    def __getattr__(self, name):
        # Callers reach past analyze() for the analyser's own helpers.
        return getattr(self._structured, name)

    def analyze(self, text, context, selection_reader=None) -> EditIntent:
        try:
            return self._structured.analyze(text, context, selection_reader)
        except Stage5EditError:
            remembered = self._recall(text, context)
            if remembered is not None:
                return remembered
            translation = self._translate(text, context)
            if translation is None:
                proposal = self._propose(text, context)
                if proposal is not None:
                    raise Stage5EditError(
                        "이 요청에 딱 맞는 기능은 없지만, 아래 순서로는 "
                        "할 수 있습니다.\n"
                        f"{proposal.as_text()}\n"
                        "한 단계씩 말씀해주시면 그대로 해드리겠습니다."
                    )
                raise
            return EditIntent(
                operation=translation.operation,
                params=translation.params,
                description=translation.description,
                source="provider",
            )

    def _recall(self, text, context) -> EditIntent | None:
        """A wording translated once before does not need translating again."""
        if self._memory is None:
            return None
        app_type = str((context or {}).get("app_type") or "").casefold()
        if app_type not in OPERATION_GUIDES:
            return None
        found = self._memory.recall(app_type, text)
        if found is None:
            return None
        operation, params = found
        if operation not in self._supported:
            # The allow-list can shrink between sessions; a note for an
            # operation this adapter no longer runs is not usable.
            return None
        return EditIntent(
            operation=operation,
            params=dict(params),
            description=str(text),
            source="memory",
        )

    def _propose(self, text, context):
        """Offer an ordering of existing operations, and refuse anyway.

        Layer 3 suggests; it does not act. A plan the reader has not seen is
        a plan the reader has not approved, so the proposal is attached to
        the same refusal they would have got, for them to decide on.
        """
        if self._composer is None:
            return None
        app_type = str((context or {}).get("app_type") or "").casefold()
        if app_type not in OPERATION_GUIDES:
            return None
        try:
            proposal = self._composer.compose(
                text, self._supported, app_type, context
            )
        except Exception:
            return None
        self.last_proposal = proposal
        return proposal

    def _translate(self, text, context) -> Translation | None:
        if self._translator is None:
            return None
        app_type = str((context or {}).get("app_type") or "").casefold()
        if app_type not in OPERATION_GUIDES:
            return None
        return self._translator.translate(
            text, self._supported, app_type, context
        )


def translator_for(llm_engine) -> LlmEditIntentTranslator | None:
    """Wrap the configured provider, or nothing when there is none."""
    caller = provider_caller(llm_engine)
    return None if caller is None else LlmEditIntentTranslator(caller)


def assisted_analyzer(
    translator, supported_operations, memory=None, composer=None
):
    """Put the provider behind the stage rules, or leave the rules alone."""
    if translator is None and memory is None and composer is None:
        return None
    from engine.edit_mode.stage6 import StructuredStage6IntentAnalyzer

    return LlmAssistedEditIntentAnalyzer(
        StructuredStage6IntentAnalyzer(),
        translator,
        supported_operations=supported_operations,
        memory=memory,
        composer=composer,
    )


__all__ = [
    "EXCEL_OPERATION_GUIDE",
    "HWP_OPERATION_GUIDE",
    "OPERATION_GUIDES",
    "POWERPOINT_OPERATION_GUIDE",
    "WORD_OPERATION_GUIDE",
    "assisted_analyzer",
    "describe_selection",
    "translator_for",
    "LlmAssistedEditIntentAnalyzer",
    "LlmEditIntentTranslator",
    "Translation",
    "build_prompt",
]
