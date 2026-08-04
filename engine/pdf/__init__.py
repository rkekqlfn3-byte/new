"""Structured, viewer-independent PDF contracts."""

from engine.pdf.analysis import (
    PdfHeadingCandidate,
    PdfTableCandidate,
    PdfTextChunk,
    chunk_pdf_pages,
    citation_label,
    find_pdf_headings,
    find_pdf_tables,
)
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
from engine.pdf.grounded_answer import (
    PdfExternalTransferPlan,
    PdfGroundedAnswer,
    PdfGroundedAnswerService,
    build_transfer_plan,
)
from engine.pdf.intake import PdfConnectionError, PdfIntakeManager
from engine.pdf.intent import (
    PdfCommandIntent,
    PdfCommandRequest,
    PdfIntentError,
    PdfIntentKind,
    looks_like_pdf_command,
    parse_pdf_intent,
)
from engine.pdf.office_workflow import PdfExcelTableWriter, PdfOfficeWorkflow
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
from engine.pdf.task_service import PdfTaskError, PdfTaskService
from engine.pdf.transformation_service import (
    PdfTransformationCancelled,
    PdfTransformationError,
    PdfTransformationService,
)

__all__ = [
    "PdfHeadingCandidate",
    "PdfTableCandidate",
    "PdfTextChunk",
    "PDF_CONTRACT_SCHEMA_VERSION",
    "PdfContractError",
    "PdfDocumentKind",
    "PdfDocumentSnapshot",
    "PdfExtractionMethod",
    "PdfExtractionResult",
    "PdfExcelTableWriter",
    "PdfExternalTransferPlan",
    "PdfGroundedAnswer",
    "PdfGroundedAnswerService",
    "PdfPageText",
    "PdfOfficeWorkflow",
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
    "PdfTaskError",
    "PdfTaskService",
    "PdfTransformationCancelled",
    "PdfTransformationError",
    "PdfTransformationService",
    "PdfReferenceError",
    "PdfTargetReference",
    "MAX_PDF_FILE_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PDF_PAGE_STREAM_BYTES",
    "MAX_PDF_TEXT_CHARS",
    "normalize_page_selection",
    "build_transfer_plan",
    "chunk_pdf_pages",
    "citation_label",
    "find_pdf_headings",
    "find_pdf_tables",
    "looks_like_pdf_command",
    "parse_pdf_intent",
    "read_pdf_document",
    "resolve_pdf_reference",
    "search_pdf_text",
]
