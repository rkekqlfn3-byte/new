"""Structured, viewer-independent PDF contracts."""

from engine.pdf.contracts import (
    PDF_CONTRACT_SCHEMA_VERSION,
    PdfContractError,
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfExtractionMethod,
    PdfExtractionResult,
    PdfPageText,
)
from engine.pdf.errors import PdfReadError, PdfReadErrorCode
from engine.pdf.intake import PdfConnectionError, PdfIntakeManager
from engine.pdf.intent import (
    PdfCommandIntent,
    PdfCommandRequest,
    PdfIntentError,
    PdfIntentKind,
    looks_like_pdf_command,
    parse_pdf_intent,
)
from engine.pdf.page_selection import normalize_page_selection
from engine.pdf.reader import (
    MAX_PDF_FILE_BYTES,
    MAX_PDF_PAGE_STREAM_BYTES,
    MAX_PDF_PAGES,
    MAX_PDF_TEXT_CHARS,
    PdfReadLimits,
    read_pdf_document,
)
from engine.pdf.reference import (
    PdfReferenceError,
    PdfTargetReference,
    resolve_pdf_reference,
)
from engine.pdf.search import PdfSearchMatch, PdfSearchResult, search_pdf_text

__all__ = [
    "PDF_CONTRACT_SCHEMA_VERSION",
    "PdfContractError",
    "PdfDocumentKind",
    "PdfDocumentSnapshot",
    "PdfExtractionMethod",
    "PdfExtractionResult",
    "PdfPageText",
    "PdfConnectionError",
    "PdfCommandIntent",
    "PdfCommandRequest",
    "PdfIntakeManager",
    "PdfIntentError",
    "PdfIntentKind",
    "PdfReadError",
    "PdfReadErrorCode",
    "PdfReadLimits",
    "PdfSearchMatch",
    "PdfSearchResult",
    "PdfReferenceError",
    "PdfTargetReference",
    "MAX_PDF_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_STREAM_BYTES",
    "MAX_PDF_TEXT_CHARS",
    "normalize_page_selection",
    "looks_like_pdf_command",
    "parse_pdf_intent",
    "read_pdf_document",
    "resolve_pdf_reference",
    "search_pdf_text",
]
