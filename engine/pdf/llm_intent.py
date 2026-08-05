"""Translate a PDF command the intent patterns did not recognise.

``parse_pdf_intent`` matches a fixed list of wordings — 병합, 분할, 회전,
요약 and so on — so a reader who says 겹쳐줘 instead of 합쳐줘 gets told to
say what they want, even though merging is supported.

This is the same idea as the edit-mode fallback in
``engine.edit_mode.llm_intent`` and shares its safety argument: the model
picks one kind from a closed list and never reads or writes the document.
``PdfCommandIntent.__post_init__`` then validates the result — the search
query, the rotation angle, which output kinds each intent may produce —
so a translation that does not satisfy the same rules a matched pattern
must satisfy is discarded rather than acted on.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Mapping

from engine.pdf.intent import PdfCommandIntent, PdfIntentKind

# What each kind is for, in the reader's language rather than the enum's.
PDF_INTENT_GUIDE: dict[str, str] = {
    "summary": "PDF 내용 요약",
    "explain": "PDF 내용을 쉽게 설명",
    "search": "PDF 안에서 단어나 문장 찾기. query=찾을 말 (필수)",
    "page_count": "쪽 수 확인",
    "table_of_contents": "목차 확인",
    "table_extract": (
        "표 추출. 엑셀 파일로 만들어달라는 말이면 output_kinds=[\"excel\"]"
    ),
    "report": (
        "보고서·발표자료 만들기. output_kinds 는 word, hwp, powerpoint 중 "
        "요청한 것들 (기본 [\"word\"])"
    ),
    "split": "PDF 나누기",
    "merge": "PDF 합치기",
    "rotate": "쪽 돌리기. rotation_degrees 는 90, 180, 270 중 하나",
    "undo": "방금 만든 PDF 결과 되돌리기",
}

UNSUPPORTED = "unsupported"

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def build_prompt() -> str:
    """The instruction the model answers, listing only what it may choose."""
    lines = [f"- {kind}: {text}" for kind, text in PDF_INTENT_GUIDE.items()]
    return (
        "너는 PDF 관련 명령을 아래 작업 중 하나로 번역한다.\n"
        "목록에 없는 기능을 요청하면 반드시 "
        f'"{UNSUPPORTED}" 를 쓴다. 작업 이름을 지어내지 마라.\n\n'
        "작업 목록:\n" + "\n".join(lines) + "\n\n"
        '출력은 JSON 한 줄만: {"kind": "...", "query": null, '
        '"output_kinds": [], "rotation_degrees": null}\n'
        "해당 없는 항목은 null 또는 빈 목록으로 둔다. 설명은 붙이지 마라."
    )


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
    inner = candidate.get("response") if isinstance(candidate, Mapping) else None
    if isinstance(inner, str):
        match = _JSON_OBJECT.search(inner)
        if match:
            try:
                candidate = json.loads(match.group(0))
            except (TypeError, ValueError):
                return {}
    return candidate if isinstance(candidate, Mapping) else {}


class PdfIntentTranslator:
    """Turn one unrecognised PDF command into a real intent, or nothing."""

    def __init__(self, caller: Callable[[str, str], Any]):
        self._caller = caller

    def translate(self, command: str) -> PdfCommandIntent | None:
        if not str(command or "").strip():
            return None
        try:
            raw = self._caller(build_prompt(), str(command))
        except Exception:
            return None
        payload = _payload(raw)
        kind = str(payload.get("kind") or "").strip()
        if kind not in PDF_INTENT_GUIDE:
            return None
        rotation = payload.get("rotation_degrees")
        outputs = payload.get("output_kinds") or ()
        try:
            return PdfCommandIntent(
                kind=PdfIntentKind(kind),
                query=payload.get("query") or None,
                output_kinds=tuple(outputs) if isinstance(outputs, (list, tuple)) else (),
                rotation_degrees=int(rotation) if isinstance(rotation, int) else None,
            )
        except (TypeError, ValueError):
            # The intent validates itself: a search with no query, a rotation
            # that is not a quarter turn, an output kind that intent cannot
            # produce. A translation that fails those checks is discarded for
            # exactly the reason a matched pattern would have been.
            return None


def translator_for(llm_engine) -> PdfIntentTranslator | None:
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
            return ""
        provider = str(ai_config.get("provider") or "openai").casefold()
        call = (
            llm_engine._call_gemini
            if provider == "gemini"
            else llm_engine._call_openai
        )
        return call(key, prompt, text, mode="json", temperature=0.0)

    return PdfIntentTranslator(ask)


__all__ = [
    "PDF_INTENT_GUIDE",
    "PdfIntentTranslator",
    "build_prompt",
    "translator_for",
]
