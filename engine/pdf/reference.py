"""Resolve Korean PDF page and deictic references against one connection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.pdf.context import PdfConnection
from engine.pdf.page_selection import normalize_page_selection
from engine.pdf.search import PdfSearchResult

_RANGE_RE = re.compile(r"(\d+)\s*(?:[-~–]|에서)\s*(\d+)\s*(?:페이지|쪽)")
_LIST_RE = re.compile(r"((?:\d+\s*,\s*)+\d+)\s*(?:페이지|쪽)")
_PAGE_RE = re.compile(r"(\d+)\s*(?:페이지|쪽)")
_QUOTED_PDF_NAME_RE = re.compile(r"""["']([^"'<>|]+\.pdf)["']""", re.IGNORECASE)
_BARE_PDF_NAME_RE = re.compile(r"([^\s\"'<>|]+\.pdf)", re.IGNORECASE)
_LAST_SEARCH_RE = re.compile(r"(?:방금\s*찾|찾은\s*부분|검색\s*결과|그\s*부분)")
_HERE_RE = re.compile(r"(?:여기|이\s*부분)")
_RELATIVE_PAGE_RE = re.compile(r"(앞|이전|전|다음|뒤)\s*(?:페이지|쪽)")
_CONNECTED_RE = re.compile(
    r"(?:pdf|이거|문서|이\s*자료|현재\s*자료|연결(?:한|된)?\s*자료)", re.IGNORECASE
)
_CONNECTION_ID_RE = re.compile(r"^pdf-connection-[a-f0-9]{32}$")
_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{64}$")


class PdfReferenceError(RuntimeError):
    status = "clarification_required"
    error_type = "validation_error"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "pdf_reference_error")

    def to_evidence_dict(self) -> dict[str, str]:
        return {"code": self.code, "status": self.status}


@dataclass(frozen=True)
class PdfTargetReference:
    connection_id: str
    document_fingerprint: str
    page_numbers: tuple[int, ...]
    source: str

    def __post_init__(self):
        if not _CONNECTION_ID_RE.fullmatch(str(self.connection_id or "")):
            raise ValueError("PDF target connection ID is invalid.")
        if not _FINGERPRINT_RE.fullmatch(str(self.document_fingerprint or "")):
            raise ValueError("PDF target fingerprint is invalid.")
        pages = tuple(self.page_numbers)
        if (
            not pages
            or pages != tuple(sorted(set(pages)))
            or any(type(page) is not int or page < 1 for page in pages)
        ):
            raise ValueError("PDF target pages must be sorted unique integers.")
        if self.source not in {
            "connected_document",
            "explicit_pages",
            "last_search",
            "current_reference",
            "previous_page",
            "next_page",
        }:
            raise ValueError("Unsupported PDF target reference source.")
        object.__setattr__(self, "page_numbers", pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "document_fingerprint": self.document_fingerprint,
            "page_numbers": list(self.page_numbers),
            "source": self.source,
        }


def _named_pdf(command: str) -> str:
    match = _QUOTED_PDF_NAME_RE.search(command) or _BARE_PDF_NAME_RE.search(command)
    if not match:
        return ""
    return Path(match.group(1).rstrip(".,!?()[]{}")).name


def _explicit_pages(command: str, page_count: int) -> tuple[int, ...]:
    pages: list[int] = []
    masked = command
    for match in _RANGE_RE.finditer(command):
        start = int(match.group(1))
        end = int(match.group(2))
        if start > end:
            normalize_page_selection(f"{start}-{end}", page_count)
        pages.extend(range(start, end + 1))
        masked = masked.replace(match.group(0), " ")
    for match in _LIST_RE.finditer(masked):
        pages.extend(int(value.strip()) for value in match.group(1).split(","))
        masked = masked.replace(match.group(0), " ")
    pages.extend(int(match.group(1)) for match in _PAGE_RE.finditer(masked))
    if not pages:
        return ()
    return normalize_page_selection(pages, page_count)


def _last_search_pages(search: PdfSearchResult | None, connection: PdfConnection):
    if search is None:
        raise PdfReferenceError(
            "last_search_missing",
            "이 PDF에서 직전에 찾은 결과가 없습니다. 검색어를 다시 알려주세요.",
        )
    if search.document.document_fingerprint != connection.document.document_fingerprint:
        raise PdfReferenceError(
            "last_search_document_changed",
            "직전 검색 결과가 현재 연결된 PDF와 다릅니다. 다시 검색해주세요.",
        )
    pages = tuple(sorted({item.page_number for item in search.matches}))
    if not pages:
        raise PdfReferenceError(
            "last_search_empty",
            "직전 PDF 검색 결과가 비어 있습니다. 다른 검색어를 알려주세요.",
        )
    return pages


def _relative_pages(
    command: str,
    reference: PdfTargetReference | None,
    connection: PdfConnection,
) -> tuple[tuple[int, ...], str] | None:
    match = _RELATIVE_PAGE_RE.search(command)
    if not match:
        return None
    if reference is None or not reference.page_numbers:
        raise PdfReferenceError(
            "relative_page_anchor_missing",
            "기준 페이지가 없습니다. 먼저 페이지 번호를 알려주세요.",
        )
    previous = match.group(1) in {"앞", "이전", "전"}
    page = min(reference.page_numbers) - 1 if previous else max(reference.page_numbers) + 1
    if page < 1 or page > connection.document.page_count:
        raise PdfReferenceError(
            "relative_page_out_of_range",
            "요청한 앞뒤 페이지가 PDF 범위를 벗어납니다.",
        )
    return (page,), "previous_page" if previous else "next_page"


def resolve_pdf_reference(
    command: str,
    connection: PdfConnection,
    *,
    last_search: PdfSearchResult | None = None,
    last_reference: PdfTargetReference | None = None,
) -> PdfTargetReference:
    """Resolve one command without guessing a PDF other than the connection."""
    if not isinstance(connection, PdfConnection):
        raise TypeError("connection must be a PdfConnection.")
    text = str(command or "").strip()
    if not text:
        raise PdfReferenceError("empty_reference", "PDF 요청 내용을 입력해주세요.")
    named = _named_pdf(text)
    if named and named.casefold() != connection.document_name.casefold():
        raise PdfReferenceError(
            "named_document_mismatch",
            "요청한 PDF가 현재 연결된 PDF와 다릅니다. 해당 PDF를 먼저 연결해주세요.",
        )
    pages = _explicit_pages(text, connection.document.page_count)
    if pages:
        source = "explicit_pages"
    elif _LAST_SEARCH_RE.search(text):
        pages = _last_search_pages(last_search, connection)
        source = "last_search"
    elif relative := _relative_pages(text, last_reference, connection):
        pages, source = relative
    elif _HERE_RE.search(text):
        if last_reference is None or not last_reference.page_numbers:
            raise PdfReferenceError(
                "current_reference_missing",
                "'여기'가 가리키는 PDF 페이지가 없습니다. 페이지 번호를 알려주세요.",
            )
        pages = last_reference.page_numbers
        source = "current_reference"
    elif named or _CONNECTED_RE.search(text):
        pages = tuple(range(1, connection.document.page_count + 1))
        source = "connected_document"
    else:
        raise PdfReferenceError(
            "pdf_reference_missing",
            "현재 연결된 PDF를 뜻하는지 확인할 수 없습니다. '이 PDF'처럼 말해주세요.",
        )
    return PdfTargetReference(
        connection_id=connection.connection_id,
        document_fingerprint=connection.document.document_fingerprint,
        page_numbers=pages,
        source=source,
    )
