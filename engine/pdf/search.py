"""Literal page-aware search over structured PDF extraction results."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from engine.pdf.contracts import PdfDocumentSnapshot, PdfExtractionResult
from engine.pdf.errors import PdfReadError, PdfReadErrorCode

MAX_PDF_SEARCH_QUERY_CHARS = 256
MAX_PDF_SEARCH_MATCHES = 500
MAX_PDF_SEARCH_CONTEXT_CHARS = 500


def _search_error(message: str) -> PdfReadError:
    return PdfReadError(PdfReadErrorCode.SEARCH_QUERY_INVALID, message)


def _query(value: Any) -> str:
    text = str(value or "").strip()
    if not text or "\x00" in text or len(text) > MAX_PDF_SEARCH_QUERY_CHARS:
        raise _search_error("PDF search query is empty or too large.")
    return text


@dataclass(frozen=True)
class PdfSearchMatch:
    page_number: int
    start_offset: int
    end_offset: int
    excerpt: str

    def __post_init__(self):
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("PDF search page number must be positive.")
        if type(self.start_offset) is not int or type(self.end_offset) is not int:
            raise ValueError("PDF search offsets must be integers.")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("PDF search offsets are invalid.")
        if not isinstance(self.excerpt, str):
            raise ValueError("PDF search excerpt must be text.")
        if "\x00" in self.excerpt or len(self.excerpt) > MAX_PDF_SEARCH_CONTEXT_CHARS:
            raise ValueError("PDF search excerpt exceeds the safe limit.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "excerpt": self.excerpt,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "excerpt_character_count": len(self.excerpt),
            "excerpt_fingerprint": hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class PdfSearchResult:
    document: PdfDocumentSnapshot
    query: str
    searched_pages: tuple[int, ...]
    matches: tuple[PdfSearchMatch, ...]
    case_sensitive: bool
    truncated: bool

    def __post_init__(self):
        if not isinstance(self.document, PdfDocumentSnapshot):
            raise ValueError("PDF search result requires a document snapshot.")
        normalized_query = _query(self.query)
        pages = tuple(self.searched_pages)
        matches = tuple(self.matches)
        if pages != tuple(sorted(set(pages))):
            raise ValueError("PDF searched pages must be sorted and unique.")
        if any(type(page) is not int or page < 1 for page in pages):
            raise ValueError("PDF searched page number is invalid.")
        if pages and pages[-1] > self.document.page_count:
            raise ValueError("PDF searched page exceeds the document page count.")
        if any(not isinstance(item, PdfSearchMatch) for item in matches):
            raise ValueError("PDF search match contract is invalid.")
        ordering = tuple((item.page_number, item.start_offset) for item in matches)
        if ordering != tuple(sorted(ordering)):
            raise ValueError("PDF search matches must be sorted.")
        if any(item.page_number not in pages for item in matches):
            raise ValueError("PDF search match is outside the searched pages.")
        if type(self.case_sensitive) is not bool or type(self.truncated) is not bool:
            raise ValueError("PDF search flags must be booleans.")
        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(self, "searched_pages", pages)
        object.__setattr__(self, "matches", matches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "query": self.query,
            "searched_pages": list(self.searched_pages),
            "matches": [item.to_dict() for item in self.matches],
            "case_sensitive": self.case_sensitive,
            "truncated": self.truncated,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_evidence_dict(),
            "query_character_count": len(self.query),
            "query_fingerprint": hashlib.sha256(self.query.encode("utf-8")).hexdigest(),
            "searched_pages": list(self.searched_pages),
            "matches": [item.to_evidence_dict() for item in self.matches],
            "case_sensitive": self.case_sensitive,
            "truncated": self.truncated,
        }


def _excerpt(text: str, start: int, end: int, context_chars: int) -> str:
    match_length = end - start
    context_budget = max(0, (MAX_PDF_SEARCH_CONTEXT_CHARS - match_length) // 2)
    radius = min(context_chars, context_budget)
    excerpt_start = max(0, start - radius)
    excerpt_end = min(len(text), end + radius)
    return text[excerpt_start:excerpt_end].strip()


def search_pdf_text(
    extraction: PdfExtractionResult,
    query: str,
    *,
    case_sensitive: bool = False,
    context_chars: int = 80,
    max_matches: int = MAX_PDF_SEARCH_MATCHES,
) -> PdfSearchResult:
    """Search extracted pages literally and return page, offset, and context."""
    if not isinstance(extraction, PdfExtractionResult):
        raise TypeError("extraction must be a PdfExtractionResult.")
    normalized_query = _query(query)
    if type(case_sensitive) is not bool:
        raise TypeError("case_sensitive must be a boolean.")
    if type(context_chars) is not int or not 0 <= context_chars <= 200:
        raise ValueError("PDF search context must be between 0 and 200 characters.")
    if type(max_matches) is not int or not 1 <= max_matches <= MAX_PDF_SEARCH_MATCHES:
        raise ValueError("PDF search match limit is invalid.")
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(normalized_query), flags)
    matches: list[PdfSearchMatch] = []
    truncated = False
    for page in extraction.pages:
        for match in pattern.finditer(page.text):
            if len(matches) >= max_matches:
                truncated = True
                break
            matches.append(
                PdfSearchMatch(
                    page_number=page.page_number,
                    start_offset=match.start(),
                    end_offset=match.end(),
                    excerpt=_excerpt(page.text, match.start(), match.end(), context_chars),
                )
            )
        if truncated:
            break
    return PdfSearchResult(
        document=extraction.document,
        query=normalized_query,
        searched_pages=extraction.requested_pages,
        matches=tuple(matches),
        case_sensitive=case_sensitive,
        truncated=truncated,
    )
