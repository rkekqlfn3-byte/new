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

__all__ = [
    "PDF_CONTRACT_SCHEMA_VERSION",
    "PdfContractError",
    "PdfDocumentKind",
    "PdfDocumentSnapshot",
    "PdfExtractionMethod",
    "PdfExtractionResult",
    "PdfPageText",
]
