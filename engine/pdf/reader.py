"""Bounded, page-aware PDF reader built on pypdf."""

from __future__ import annotations

import hashlib
import os
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - exercised by dependency injection tests
    PdfReader = None

from engine.pdf.contracts import (
    MAX_PAGE_TEXT_CHARS,
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfExtractionMethod,
    PdfExtractionResult,
    PdfPageText,
)
from engine.pdf.errors import PdfReadError, PdfReadErrorCode
from engine.pdf.page_selection import normalize_page_selection

MAX_PDF_FILE_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MAX_PDF_TEXT_CHARS = 5_000_000
MAX_PDF_PAGE_STREAM_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class PdfReadLimits:
    max_file_bytes: int = MAX_PDF_FILE_BYTES
    max_pages: int = MAX_PDF_PAGES
    max_total_text_chars: int = MAX_PDF_TEXT_CHARS
    max_page_text_chars: int = MAX_PAGE_TEXT_CHARS
    max_page_stream_bytes: int = MAX_PDF_PAGE_STREAM_BYTES
    timeout_seconds: float = 30.0
    hash_chunk_bytes: int = 1024 * 1024

    def __post_init__(self):
        bounded_fields = (
            "max_file_bytes",
            "max_pages",
            "max_total_text_chars",
            "max_page_text_chars",
            "max_page_stream_bytes",
        )
        if any(
            type(getattr(self, name)) is not int or getattr(self, name) < 0
            for name in bounded_fields
        ):
            raise ValueError("PDF read safety limits must be non-negative integers.")
        if type(self.hash_chunk_bytes) is not int or self.hash_chunk_bytes < 1:
            raise ValueError("PDF hash chunk size must be a positive integer.")
        if self.max_page_text_chars > MAX_PAGE_TEXT_CHARS:
            raise ValueError("PDF page text limit exceeds the contract maximum.")
        if type(self.timeout_seconds) not in {int, float} or self.timeout_seconds <= 0:
            raise ValueError("PDF read timeout must be positive.")


class _ReadGuard:
    def __init__(self, timeout_seconds: float, cancel_check: Callable[[], bool] | None):
        self.deadline = time.monotonic() + timeout_seconds
        self.cancel_check = cancel_check

    def check(self):
        if self.cancel_check is not None and self.cancel_check():
            raise PdfReadError(PdfReadErrorCode.CANCELLED, "PDF reading was cancelled.")
        if time.monotonic() > self.deadline:
            raise PdfReadError(PdfReadErrorCode.TIMEOUT, "PDF reading exceeded its time limit.")


def _resolve_pdf_path(file_path: str | os.PathLike[str]) -> Path:
    text = os.path.expandvars(os.path.expanduser(str(file_path or "").strip()))
    if not text or "\x00" in text or text.startswith(("\\\\", "//")):
        raise PdfReadError(PdfReadErrorCode.UNSAFE_PATH, "PDF path must be a local path.")
    candidate = Path(text)
    if not candidate.is_absolute():
        raise PdfReadError(PdfReadErrorCode.UNSAFE_PATH, "PDF path must be absolute.")
    if candidate.is_symlink():
        raise PdfReadError(PdfReadErrorCode.UNSAFE_PATH, "PDF path cannot be a symbolic link.")
    try:
        path = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise PdfReadError(PdfReadErrorCode.FILE_NOT_FOUND, "PDF file was not found.") from error
    except OSError as error:
        raise PdfReadError(PdfReadErrorCode.IO_ERROR, "PDF path could not be resolved.") from error
    if not path.is_file() or path.suffix.casefold() != ".pdf":
        raise PdfReadError(PdfReadErrorCode.UNSAFE_PATH, "PDF path must name a .pdf file.")
    return path


def _file_fingerprint(path: Path, guard: _ReadGuard, chunk_size: int) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                guard.check()
                digest.update(chunk)
    except PdfReadError:
        raise
    except OSError as error:
        raise PdfReadError(PdfReadErrorCode.IO_ERROR, "PDF file could not be read.") from error
    guard.check()
    return digest.hexdigest()


def _source_stat(path: Path):
    try:
        return path.stat()
    except OSError as error:
        raise PdfReadError(PdfReadErrorCode.IO_ERROR, "PDF metadata could not be read.") from error


def _assert_source_unchanged(path: Path, expected_stat):
    current = _source_stat(path)
    expected = (expected_stat.st_size, expected_stat.st_mtime_ns)
    actual = (current.st_size, current.st_mtime_ns)
    if actual != expected:
        raise PdfReadError(PdfReadErrorCode.SOURCE_CHANGED, "PDF changed while it was read.")


def _object_value(value: Any) -> Any:
    getter = getattr(value, "get_object", None)
    return getter() if callable(getter) else value


def _content_streams(page: Any) -> tuple[Any, ...]:
    try:
        contents = _object_value(page.raw_get("/Contents"))
    except (KeyError, AttributeError):
        return ()
    values = contents if isinstance(contents, (list, tuple)) else (contents,)
    return tuple(_object_value(item) for item in values)


def _stream_filters(stream: Any) -> tuple[str, ...]:
    try:
        value = _object_value(stream.get("/Filter"))
    except AttributeError:
        return ()
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple)) else (value,)
    return tuple(str(_object_value(item)) for item in values)


def _bounded_inflated_size(data: bytes, limit: int) -> int:
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(data, limit + 1)
        if len(decoded) > limit or decoder.unconsumed_tail:
            return limit + 1
        flushed = decoder.flush(limit - len(decoded) + 1)
    except zlib.error as error:
        raise PdfReadError(
            PdfReadErrorCode.EXTRACTION_FAILED,
            "PDF compressed content stream is malformed.",
        ) from error
    return len(decoded) + len(flushed)


def _bounded_stream_size(page: Any, limit: int) -> int:
    total = 0
    for stream in _content_streams(page):
        data = getattr(stream, "_data", None)
        if not isinstance(data, bytes):
            raise PdfReadError(
                PdfReadErrorCode.EXTRACTION_FAILED,
                "PDF content stream could not be inspected safely.",
            )
        filters = _stream_filters(stream)
        if not filters:
            size = len(data)
        elif filters in {("/FlateDecode",), ("/Fl",)}:
            size = _bounded_inflated_size(data, max(0, limit - total))
        else:
            raise PdfReadError(
                PdfReadErrorCode.CONTENT_FILTER_UNSUPPORTED,
                "PDF content stream uses an unsupported filter chain.",
            )
        total += size
        if total > limit:
            return total
    return total


def _open_reader(stream: Any):
    if PdfReader is None:
        raise PdfReadError(
            PdfReadErrorCode.DEPENDENCY_UNAVAILABLE,
            "pypdf is not installed.",
        )
    try:
        return PdfReader(stream, strict=False)
    except Exception as error:
        raise PdfReadError(
            PdfReadErrorCode.MALFORMED_DOCUMENT,
            "PDF structure is malformed.",
        ) from error


def _reader_page_count(reader: Any, limits: PdfReadLimits) -> int:
    if bool(getattr(reader, "is_encrypted", False)):
        raise PdfReadError(
            PdfReadErrorCode.PASSWORD_REQUIRED,
            "Encrypted PDF requires a password.",
        )
    try:
        page_count = len(reader.pages)
    except Exception as error:
        raise PdfReadError(
            PdfReadErrorCode.MALFORMED_DOCUMENT,
            "PDF page tree is malformed.",
        ) from error
    if page_count > limits.max_pages:
        raise PdfReadError(
            PdfReadErrorCode.PAGE_LIMIT_EXCEEDED,
            "PDF 페이지 수가 안전 제한을 초과했습니다.",
        )
    if page_count == 0:
        raise PdfReadError(
            PdfReadErrorCode.EMPTY_DOCUMENT,
            "PDF document has no pages.",
        )
    return page_count


def _extract_page(reader: Any, page_number: int, limits: PdfReadLimits) -> PdfPageText:
    try:
        page = reader.pages[page_number - 1]
        if _bounded_stream_size(page, limits.max_page_stream_bytes) > limits.max_page_stream_bytes:
            raise PdfReadError(
                PdfReadErrorCode.CONTENT_STREAM_TOO_LARGE,
                "PDF 페이지 content stream이 안전 제한을 초과했습니다.",
                page_number=page_number,
            )
        extracted = page.extract_text() or ""
    except PdfReadError:
        raise
    except Exception as error:
        raise PdfReadError(
            PdfReadErrorCode.EXTRACTION_FAILED,
            "PDF page text extraction failed.",
            page_number=page_number,
        ) from error
    text = extracted if extracted.strip() else ""
    if len(text) > limits.max_page_text_chars:
        raise PdfReadError(
            PdfReadErrorCode.TEXT_LIMIT_EXCEEDED,
            "PDF 추출 텍스트가 안전 제한을 초과했습니다.",
            page_number=page_number,
        )
    if not text:
        return PdfPageText.from_text(
            page_number,
            "",
            extraction_method=PdfExtractionMethod.NONE,
            warnings=("no_text_layer",),
        )
    return PdfPageText.from_text(page_number, text)


def _document_kind(pages: tuple[PdfPageText, ...], complete: bool) -> PdfDocumentKind:
    if not complete or not pages:
        return PdfDocumentKind.UNKNOWN
    populated = sum(bool(page.text.strip()) for page in pages)
    if populated == len(pages):
        return PdfDocumentKind.TEXT
    if populated == 0:
        return PdfDocumentKind.SCANNED
    return PdfDocumentKind.MIXED


def _document_warnings(kind: PdfDocumentKind, complete: bool) -> tuple[str, ...]:
    if not complete:
        return ("partial_document_kind_unknown",)
    if kind is PdfDocumentKind.SCANNED:
        return ("ocr_required",)
    if kind is PdfDocumentKind.MIXED:
        return ("partial_text_layer",)
    return ()


def read_pdf_document(
    file_path: str | os.PathLike[str],
    *,
    pages: Any = None,
    limits: PdfReadLimits | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> PdfExtractionResult:
    """Read selected pages into a structured, path-free PDF extraction result."""
    active_limits = limits or PdfReadLimits()
    if not isinstance(active_limits, PdfReadLimits):
        raise TypeError("limits must be a PdfReadLimits instance.")
    guard = _ReadGuard(active_limits.timeout_seconds, cancel_check)
    path = _resolve_pdf_path(file_path)
    initial_stat = _source_stat(path)
    if initial_stat.st_size > active_limits.max_file_bytes:
        raise PdfReadError(
            PdfReadErrorCode.FILE_TOO_LARGE,
            "PDF 파일 크기가 안전 제한을 초과했습니다.",
        )
    fingerprint = _file_fingerprint(path, guard, active_limits.hash_chunk_bytes)
    _assert_source_unchanged(path, initial_stat)
    try:
        with path.open("rb") as stream:
            reader = _open_reader(stream)
            page_count = _reader_page_count(reader, active_limits)
            requested = normalize_page_selection(pages, page_count)
            extracted_pages = []
            total_chars = 0
            for page_number in requested:
                guard.check()
                page = _extract_page(reader, page_number, active_limits)
                total_chars += page.character_count + (1 if page.text else 0)
                if total_chars > active_limits.max_total_text_chars:
                    raise PdfReadError(
                        PdfReadErrorCode.TEXT_LIMIT_EXCEEDED,
                        "PDF 추출 텍스트가 안전 제한을 초과했습니다.",
                    )
                extracted_pages.append(page)
                guard.check()
    except PdfReadError:
        raise
    except OSError as error:
        raise PdfReadError(PdfReadErrorCode.IO_ERROR, "PDF file could not be read.") from error
    _assert_source_unchanged(path, initial_stat)
    normalized_pages = tuple(extracted_pages)
    complete = requested == tuple(range(1, page_count + 1))
    kind = _document_kind(normalized_pages, complete)
    snapshot = PdfDocumentSnapshot(
        document_id=f"pdf-{fingerprint[:24]}",
        document_fingerprint=fingerprint,
        page_count=page_count,
        file_size_bytes=initial_stat.st_size,
        encrypted=False,
        document_kind=kind,
    )
    return PdfExtractionResult(
        document=snapshot,
        pages=normalized_pages,
        requested_pages=requested,
        warnings=_document_warnings(kind, complete),
    )
