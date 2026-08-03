"""Typed, content-free failures for PDF read operations."""

from __future__ import annotations

from enum import Enum


class PdfReadErrorCode(str, Enum):
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    FILE_NOT_FOUND = "file_not_found"
    UNSAFE_PATH = "unsafe_path"
    FILE_TOO_LARGE = "file_too_large"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    EMPTY_DOCUMENT = "empty_document"
    PAGE_SELECTION_INVALID = "page_selection_invalid"
    SEARCH_QUERY_INVALID = "search_query_invalid"
    PASSWORD_REQUIRED = "password_required"
    MALFORMED_DOCUMENT = "malformed_document"
    CONTENT_STREAM_TOO_LARGE = "content_stream_too_large"
    CONTENT_FILTER_UNSUPPORTED = "content_filter_unsupported"
    TEXT_LIMIT_EXCEEDED = "text_limit_exceeded"
    SOURCE_CHANGED = "source_changed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    IO_ERROR = "io_error"
    EXTRACTION_FAILED = "extraction_failed"


_OUTCOMES = {
    PdfReadErrorCode.DEPENDENCY_UNAVAILABLE: "environment_blocked",
    PdfReadErrorCode.PASSWORD_REQUIRED: "needs_input",
    PdfReadErrorCode.CANCELLED: "cancelled",
}


class PdfReadError(RuntimeError):
    """PDF read failure whose persistent representation contains no document data."""

    def __init__(
        self,
        code: PdfReadErrorCode | str,
        message: str,
        *,
        page_number: int | None = None,
    ):
        try:
            normalized_code = PdfReadErrorCode(code)
        except ValueError as error:
            raise ValueError("Unsupported PDF read error code.") from error
        if page_number is not None and (type(page_number) is not int or page_number < 1):
            raise ValueError("PDF read error page number must be positive.")
        super().__init__(str(message or "PDF read failed."))
        self.code = normalized_code
        self.page_number = page_number

    @property
    def outcome(self) -> str:
        return _OUTCOMES.get(self.code, "validation_error")

    def to_evidence_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "code": self.code.value,
            "outcome": self.outcome,
        }
        if self.page_number is not None:
            value["page_number"] = self.page_number
        return value
