"""Page-grounded PDF chunking, heading discovery, and conservative tables."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from engine.pdf.contracts import PdfExtractionResult
from engine.pdf.errors import PdfReadError, PdfReadErrorCode

MAX_PDF_CHUNK_CHARS = 6_000
MAX_PDF_CHUNKS = 1_000
MAX_PDF_HEADING_CANDIDATES = 100
MAX_PDF_TABLE_COLUMNS = 20
MAX_PDF_TABLE_ROWS = 500
_HEADING_NUMBER = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLC]+)[.)]?\s+\S+", re.IGNORECASE)
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?%?$")


@dataclass(frozen=True)
class PdfTextChunk:
    chunk_index: int
    page_number: int
    text: str
    text_fingerprint: str

    def __post_init__(self):
        if type(self.chunk_index) is not int or self.chunk_index < 1:
            raise ValueError("PDF chunk index must be positive.")
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("PDF chunk page number must be positive.")
        text = str(self.text or "")
        if not text or "\x00" in text or len(text) > MAX_PDF_CHUNK_CHARS:
            raise ValueError("PDF chunk text is empty or exceeds its limit.")
        fingerprint = str(self.text_fingerprint or "").casefold()
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if fingerprint != expected:
            raise ValueError("PDF chunk fingerprint does not match its text.")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "text_fingerprint", fingerprint)

    @classmethod
    def from_text(cls, index: int, page_number: int, text: str) -> "PdfTextChunk":
        normalized = str(text or "")
        return cls(
            chunk_index=index,
            page_number=page_number,
            text=normalized,
            text_fingerprint=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "character_count": len(self.text),
            "text_fingerprint": self.text_fingerprint,
        }


@dataclass(frozen=True)
class PdfHeadingCandidate:
    page_number: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"page_number": self.page_number, "text": self.text}

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "character_count": len(self.text),
            "text_fingerprint": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }


@dataclass(frozen=True)
class PdfTableCandidate:
    page_number: int
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    confidence: float
    delimiter: str

    def __post_init__(self):
        headers = tuple(str(item) for item in self.headers)
        rows = tuple(tuple(str(cell) for cell in row) for row in self.rows)
        if not 2 <= len(headers) <= MAX_PDF_TABLE_COLUMNS:
            raise ValueError("PDF table column count is outside the safe range.")
        if not 1 <= len(rows) <= MAX_PDF_TABLE_ROWS:
            raise ValueError("PDF table row count is outside the safe range.")
        if any(len(row) != len(headers) for row in rows):
            raise ValueError("PDF table rows do not match the header width.")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("PDF table confidence is outside the valid range.")
        if self.delimiter not in {"tab", "whitespace"}:
            raise ValueError("Unsupported PDF table delimiter.")
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "confidence", float(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "headers": list(self.headers),
            "rows": [list(row) for row in self.rows],
            "confidence": self.confidence,
            "delimiter": self.delimiter,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        encoded = "\n".join(("\t".join(self.headers), *("\t".join(row) for row in self.rows)))
        return {
            "page_number": self.page_number,
            "column_count": len(self.headers),
            "row_count": len(self.rows),
            "confidence": self.confidence,
            "delimiter": self.delimiter,
            "table_fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }


def _split_page_text(text: str, limit: int) -> tuple[str, ...]:
    remaining = str(text or "").strip()
    chunks = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        boundary = max(
            remaining.rfind("\n", limit // 2, limit + 1),
            remaining.rfind(" ", limit // 2, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        chunks.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return tuple(item for item in chunks if item)


def chunk_pdf_pages(
    extraction: PdfExtractionResult,
    *,
    max_chunk_chars: int = MAX_PDF_CHUNK_CHARS,
) -> tuple[PdfTextChunk, ...]:
    if not isinstance(extraction, PdfExtractionResult):
        raise TypeError("extraction must be a PdfExtractionResult.")
    if type(max_chunk_chars) is not int or not 200 <= max_chunk_chars <= MAX_PDF_CHUNK_CHARS:
        raise ValueError("PDF chunk size is outside the safe range.")
    chunks = []
    for page in extraction.pages:
        if not page.text.strip():
            raise PdfReadError(
                PdfReadErrorCode.OCR_REQUIRED,
                "선택한 PDF 페이지에 텍스트 계층이 없어 OCR이 필요합니다.",
                page_number=page.page_number,
            )
        for part in _split_page_text(page.text, max_chunk_chars):
            if len(chunks) >= MAX_PDF_CHUNKS:
                raise PdfReadError(
                    PdfReadErrorCode.TEXT_LIMIT_EXCEEDED,
                    "PDF chunk count exceeds the safe limit.",
                )
            chunks.append(PdfTextChunk.from_text(len(chunks) + 1, page.page_number, part))
    if not chunks:
        raise PdfReadError(
            PdfReadErrorCode.OCR_REQUIRED,
            "선택한 PDF 페이지에서 읽을 수 있는 텍스트를 찾지 못했습니다.",
        )
    return tuple(chunks)


def find_pdf_headings(extraction: PdfExtractionResult) -> tuple[PdfHeadingCandidate, ...]:
    candidates = []
    for page in extraction.pages:
        for raw_line in page.text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not 2 <= len(line) <= 120:
                continue
            looks_numbered = bool(_HEADING_NUMBER.match(line))
            looks_short_title = len(line.split()) <= 10 and line[-1:] not in ".,;:!?"
            if not (looks_numbered or looks_short_title):
                continue
            candidates.append(PdfHeadingCandidate(page.page_number, line))
            if len(candidates) >= MAX_PDF_HEADING_CANDIDATES:
                return tuple(candidates)
    return tuple(candidates)


def _row_cells(line: str) -> tuple[tuple[str, ...], str]:
    normalized = line.strip()
    if "\t" in normalized:
        return tuple(item.strip() for item in normalized.split("\t")), "tab"
    return tuple(re.split(r"\s+", normalized)), "whitespace"


def find_pdf_tables(extraction: PdfExtractionResult) -> tuple[PdfTableCandidate, ...]:
    tables = []
    for page in extraction.pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        if not 3 <= len(lines) <= MAX_PDF_TABLE_ROWS + 1:
            continue
        parsed = [_row_cells(line) for line in lines]
        widths = {len(cells) for cells, _ in parsed}
        delimiters = {delimiter for _, delimiter in parsed}
        if len(widths) != 1 or len(delimiters) != 1:
            continue
        width = next(iter(widths))
        if not 2 <= width <= MAX_PDF_TABLE_COLUMNS:
            continue
        header = parsed[0][0]
        rows = tuple(cells for cells, _ in parsed[1:])
        if any(_NUMBER.fullmatch(value.replace(" ", "")) for value in header):
            continue
        numeric_columns = sum(
            all(_NUMBER.fullmatch(row[index].replace(" ", "")) for row in rows)
            for index in range(width)
        )
        if numeric_columns < 1:
            continue
        delimiter = next(iter(delimiters))
        confidence = 0.98 if delimiter == "tab" else 0.9
        tables.append(
            PdfTableCandidate(
                page_number=page.page_number,
                headers=header,
                rows=rows,
                confidence=confidence,
                delimiter=delimiter,
            )
        )
    return tuple(tables)


def citation_label(page_numbers) -> str:
    pages = tuple(sorted({int(page) for page in page_numbers}))
    return "근거 페이지: " + ", ".join(f"p.{page}" for page in pages)
