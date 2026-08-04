"""Deterministic, content-private intent parsing for connected PDF commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from engine.pdf.reference import PdfReferenceError, PdfTargetReference


class PdfIntentKind(str, Enum):
    SUMMARY = "summary"
    EXPLAIN = "explain"
    SEARCH = "search"
    PAGE_COUNT = "page_count"
    TABLE_OF_CONTENTS = "table_of_contents"
    TABLE_EXTRACT = "table_extract"
    REPORT = "report"
    SPLIT = "split"
    MERGE = "merge"
    ROTATE = "rotate"


_INTENT_PATTERNS = (
    (PdfIntentKind.MERGE, re.compile(r"(?:병합|합쳐|하나로\s*만들)")),
    (PdfIntentKind.SPLIT, re.compile(r"(?:분할|나눠|쪼개)")),
    (PdfIntentKind.ROTATE, re.compile(r"(?:회전|돌려)")),
    (PdfIntentKind.REPORT, re.compile(r"(?:보고서|리포트)")),
    (
        PdfIntentKind.TABLE_EXTRACT,
        re.compile(r"(?:표\s*(?:를|만)?\s*(?:추출|뽑|정리)|테이블\s*(?:추출|뽑|정리))"),
    ),
    (PdfIntentKind.TABLE_OF_CONTENTS, re.compile(r"(?:목차|차례)")),
    (
        PdfIntentKind.PAGE_COUNT,
        re.compile(r"(?:(?:총|전체)?\s*(?:페이지|쪽)\s*(?:수|개수)|몇\s*(?:페이지|쪽))"),
    ),
    (PdfIntentKind.SEARCH, re.compile(r"(?:검색|찾아|찾기|어디\s*(?:있|나오))")),
    (PdfIntentKind.SUMMARY, re.compile(r"(?:요약|핵심|간추|줄여)")),
    (
        PdfIntentKind.EXPLAIN,
        re.compile(r"(?:설명|읽어|알려|보여|내용|무슨\s*말|질문|답해|뭐야|무엇)"),
    ),
)
_QUOTED_QUERY_RE = re.compile(r"""["']([^"']{1,200})["']""")
_SEARCH_PREFIX_RE = re.compile(
    r"^(?:(?:이\s*)?(?:pdf|문서)|이거|여기|이\s*부분)(?:에서|안에서)?\s*",
    re.IGNORECASE,
)
_SEARCH_SUFFIX_RE = re.compile(r"\s*(?:검색|찾아|찾기|어디\s*(?:있|나오)).*$")
_PAGE_REFERENCE_RE = re.compile(
    r"\d+(?:\s*[-~–,]\s*\d+)*\s*(?:페이지|쪽)(?:에서|의|를|만)?|"
    r"(?:이|현재|연결된?)\s*(?:pdf|문서)|(?:여기|이\s*부분|앞|이전|다음|뒤)\s*(?:페이지|쪽)?",
    re.IGNORECASE,
)
_MUTATING_INTENTS = frozenset({PdfIntentKind.SPLIT, PdfIntentKind.MERGE, PdfIntentKind.ROTATE})
_CONNECTED_CONTEXT_RE = re.compile(
    r"(?:이거|이\s*문서|현재\s*문서|여기|이\s*부분|"
    r"(?:앞|이전|전|다음|뒤)\s*(?:페이지|쪽)|방금\s*찾|검색\s*결과|"
    r"\d+(?:\s*[-~–,]\s*\d+)*\s*(?:페이지|쪽))"
)
_PDF_EXCLUSIVE_OPERATION_RE = re.compile(
    r"(?:목차|차례|몇\s*(?:페이지|쪽)|(?:페이지|쪽)\s*(?:수|개수)|"
    r"pdf\s*(?:분할|병합|회전)|표\s*(?:를|만)?\s*(?:추출|뽑))",
    re.IGNORECASE,
)
_PDF_EXPORT_RE = re.compile(
    r"pdf\s*(?:(?:파일|형식)(?:으)?로|로).{0,20}(?:저장|내보내|변환)",
    re.IGNORECASE,
)


class PdfIntentError(PdfReferenceError):
    pass


@dataclass(frozen=True)
class PdfCommandIntent:
    kind: PdfIntentKind | str
    query: str | None = None

    def __post_init__(self):
        try:
            kind = PdfIntentKind(self.kind)
        except ValueError as error:
            raise ValueError("Unsupported PDF command intent.") from error
        query = None if self.query is None else str(self.query).strip()
        if query is not None and (not query or len(query) > 200 or "\x00" in query):
            raise ValueError("PDF search query is invalid.")
        if kind is PdfIntentKind.SEARCH and query is None:
            raise ValueError("PDF search intent requires a query.")
        if kind is not PdfIntentKind.SEARCH and query is not None:
            raise ValueError("Only PDF search intent can contain a query.")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "query", query)

    @property
    def requires_confirmation(self) -> bool:
        return self.kind in _MUTATING_INTENTS

    @property
    def implementation_stage(self) -> str:
        if self.kind is PdfIntentKind.PAGE_COUNT:
            return "PDF-2"
        if self.kind in _MUTATING_INTENTS:
            return "PDF-4"
        return "PDF-3"

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "requires_confirmation": self.requires_confirmation,
            "implementation_stage": self.implementation_stage,
            "query_present": self.query is not None,
            "query_length": len(self.query or ""),
        }


@dataclass(frozen=True)
class PdfCommandRequest:
    intent: PdfCommandIntent
    reference: PdfTargetReference

    def __post_init__(self):
        if not isinstance(self.intent, PdfCommandIntent):
            raise ValueError("PDF command request requires an intent.")
        if not isinstance(self.reference, PdfTargetReference):
            raise ValueError("PDF command request requires a target reference.")

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_evidence_dict(),
            "reference": self.reference.to_dict(),
        }


def _search_query(command: str) -> str:
    quoted = _QUOTED_QUERY_RE.search(command)
    if quoted and not quoted.group(1).casefold().endswith(".pdf"):
        return quoted.group(1).strip()
    prefix = _SEARCH_SUFFIX_RE.sub("", command).strip()
    prefix = _SEARCH_PREFIX_RE.sub("", prefix).strip()
    prefix = _PAGE_REFERENCE_RE.sub("", prefix).strip(" \t,.:;!?()[]{}")
    if not prefix or prefix.casefold() == "pdf":
        raise PdfIntentError(
            "pdf_search_query_missing",
            "PDF에서 찾을 단어나 문장을 알려주세요.",
        )
    if len(prefix) > 200 or "\x00" in prefix:
        raise PdfIntentError(
            "pdf_search_query_invalid",
            "PDF 검색어가 너무 길거나 올바르지 않습니다.",
        )
    return prefix


def parse_pdf_intent(command: str) -> PdfCommandIntent:
    text = str(command or "").strip()
    if not text:
        raise PdfIntentError("pdf_intent_missing", "PDF에서 할 일을 알려주세요.")
    for kind, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            query = _search_query(text) if kind is PdfIntentKind.SEARCH else None
            return PdfCommandIntent(kind=kind, query=query)
    raise PdfIntentError(
        "pdf_intent_missing",
        "PDF에서 요약·설명·검색 등 어떤 작업을 할지 알려주세요.",
    )


def looks_like_pdf_command(command: str, *, connected: bool) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if _PDF_EXPORT_RE.search(text):
        return False
    if re.search(
        r"(?:\.pdf(?=$|[^A-Za-z0-9])|(?<![A-Za-z0-9])pdf(?=$|[^A-Za-z0-9]))",
        text,
        re.IGNORECASE,
    ):
        return True
    if not connected or not any(pattern.search(text) for _, pattern in _INTENT_PATTERNS):
        return False
    return bool(_CONNECTED_CONTEXT_RE.search(text) or _PDF_EXCLUSIVE_OPERATION_RE.search(text))
