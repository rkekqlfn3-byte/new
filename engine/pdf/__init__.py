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
from engine.pdf.page_selection import normalize_page_selection
from engine.pdf.reader import (
    MAX_PDF_FILE_BYTES,
    MAX_PDF_PAGE_STREAM_BYTES,
    MAX_PDF_PAGES,
    MAX_PDF_TEXT_CHARS,
    PdfReadLimits,
    read_pdf_document,
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
    "PdfReadError",
    "PdfReadErrorCode",
    "PdfReadLimits",
    "PdfSearchMatch",
    "PdfSearchResult",
    "MAX_PDF_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_STREAM_BYTES",
    "MAX_PDF_TEXT_CHARS",
    "normalize_page_selection",
    "read_pdf_document",
    "search_pdf_text",
]
