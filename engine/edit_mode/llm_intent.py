"""Translate a 한글 edit command the rules did not recognise.

The rule analyser matches wordings someone wrote down in advance, so a
sentence one word away from a known one fails even when the operation it
asks for already exists.  ``번호 매겨줘`` is the plain way to ask for a
numbered list and 한글 numbering works, but no rule matched it.

Measured against the configured provider before this was written: sixteen
of sixteen unrecognised wordings — 행간, 라인 간격, 진하게, 두껍게, 센터
정렬, 가운데로 맞춰줘, 테이블 3행 4열, 표 3x4, 번호 매겨줘 among them —
came back as the right operation with the right parameters, and the three
asking for things 한글 support does not cover (쪽 번호, 맞춤법, 각주) came
back as unsupported rather than an invented operation name.

The model never touches the document.  It picks one name from a closed list
and fills that name's parameters; everything after it — preview, approval,
context fingerprint comparison, execution, verification, undo — is
unchanged.  A wrong guess is something the reader declines on the preview,
not an edit that already happened.

Only 한글 is translated here.  Excel, Word and PowerPoint were not measured,
and an unmeasured capability is not claimed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from engine.edit_mode.stage5 import EditIntent, Stage5EditError

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
}

UNSUPPORTED = "unsupported"

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def build_prompt(operations) -> str:
    """The instruction the model answers, listing only what it may choose."""
    allowed = sorted(set(operations) & set(HWP_OPERATION_GUIDE))
    lines = [
        f"- {name}: {HWP_OPERATION_GUIDE[name][0]}" for name in allowed
    ]
    return (
        "너는 한글(HWP) 문서 편집 명령을 아래 작업 중 하나로 번역한다.\n"
        "목록에 없는 기능을 요청하면 반드시 "
        f'"{UNSUPPORTED}" 를 쓴다. 작업 이름을 지어내지 마라.\n\n'
        "작업 목록:\n" + "\n".join(lines) + "\n\n"
        '출력은 JSON 한 줄만: {"operation": "...", "params": {...}, '
        '"description": "사용자에게 보여줄 한 줄 설명"}\n'
        "params 에는 위에 적힌 이름만 쓴다. 설명은 붙이지 마라."
    )


@dataclass(frozen=True, slots=True)
class Translation:
    operation: str
    params: dict[str, Any]
    description: str


def _payload(raw) -> dict:
    """Pull the object out of a reply, which providers wrap differently."""
    if isinstance(raw, Mapping):
        candidate: Any = raw
    else:
        match = _JSON_OBJECT.search(str(raw or ""))
        if not match:
            return {}
        try:
            candidate = json.loads(match.group(0))
        except (TypeError, ValueError):
            return {}
    # The command providers answer as {"response": "<json string>"}.
    inner = candidate.get("response") if isinstance(candidate, Mapping) else None
    if isinstance(inner, str):
        match = _JSON_OBJECT.search(inner)
        if match:
            try:
                candidate = json.loads(match.group(0))
            except (TypeError, ValueError):
                return {}
    return candidate if isinstance(candidate, Mapping) else {}


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

    def translate(self, text: str, operations) -> Translation | None:
        allowed = set(operations) & set(HWP_OPERATION_GUIDE)
        if not allowed or not str(text or "").strip():
            return None
        try:
            raw = self._caller(build_prompt(allowed), str(text))
        except Exception:
            # An offline machine or a provider outage must not turn an
            # unrecognised sentence into a crash.
            return None
        payload = _payload(raw)
        operation = str(payload.get("operation") or "").strip()
        if operation not in allowed:
            return None
        params = payload.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        permitted = set(HWP_OPERATION_GUIDE[operation][1])
        if not set(params) <= permitted:
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
    ):
        self._structured = structured
        self._translator = translator
        # The adapter refuses an operation outside its own allow-list after
        # analysis, so translating into one would only trade a clear message
        # for a confusing one. Ask for the same list up front.
        self._supported = frozenset(
            supported_operations
            if supported_operations is not None
            else HWP_OPERATION_GUIDE
        )

    def __getattr__(self, name):
        # Callers reach past analyze() for the analyser's own helpers.
        return getattr(self._structured, name)

    def analyze(self, text, context, selection_reader=None) -> EditIntent:
        try:
            return self._structured.analyze(text, context, selection_reader)
        except Stage5EditError:
            translation = self._translate(text, context)
            if translation is None:
                raise
            return EditIntent(
                operation=translation.operation,
                params=translation.params,
                description=translation.description,
            )

    def _translate(self, text, context) -> Translation | None:
        if self._translator is None:
            return None
        if str((context or {}).get("app_type") or "") != "hwp":
            return None
        return self._translator.translate(text, self._supported)


def translator_for(llm_engine) -> LlmEditIntentTranslator | None:
    """Wrap the configured provider, or nothing when there is none."""
    if llm_engine is None:
        return None

    def ask(prompt: str, text: str):
        manager = getattr(llm_engine, "dict_mgr", None)
        if manager is None:
            return ""
        ai_config = manager.config_manager.ai_config
        key = str(ai_config.get("api_key") or "").strip()
        if not key:
            # An unconfigured provider is the same as no provider: the rules
            # answer alone and the reader sees the usual guidance.
            return ""
        provider = str(ai_config.get("provider") or "openai").casefold()
        call = (
            llm_engine._call_gemini
            if provider == "gemini"
            else llm_engine._call_openai
        )
        return call(key, prompt, text, mode="json", temperature=0.0)

    return LlmEditIntentTranslator(ask)


def assisted_analyzer(translator, supported_operations):
    """Put the provider behind the stage rules, or leave the rules alone."""
    if translator is None:
        return None
    from engine.edit_mode.stage6 import StructuredStage6IntentAnalyzer

    return LlmAssistedEditIntentAnalyzer(
        StructuredStage6IntentAnalyzer(),
        translator,
        supported_operations=supported_operations,
    )


__all__ = [
    "HWP_OPERATION_GUIDE",
    "assisted_analyzer",
    "translator_for",
    "LlmAssistedEditIntentAnalyzer",
    "LlmEditIntentTranslator",
    "Translation",
    "build_prompt",
]
