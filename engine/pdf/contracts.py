"""Serializable PDF read contracts with content-free evidence projections."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

PDF_CONTRACT_SCHEMA_VERSION = 1
MAX_PAGE_TEXT_CHARS = 1_000_000
MAX_PDF_CONTRACT_PAGES = 100_000
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FINGERPRINT = re.compile(r"^[A-Fa-f0-9]{64}$")
_WARNING_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class PdfContractError(ValueError):
    """The PDF runtime value violates the structured contract."""


class PdfDocumentKind(str, Enum):
    UNKNOWN = "unknown"
    TEXT = "text"
    SCANNED = "scanned"
    MIXED = "mixed"


class PdfExtractionMethod(str, Enum):
    NONE = "none"
    PYPDF = "pypdf"
    OCR = "ocr"


def _timestamp(value: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise PdfContractError("PDF contract time must use ISO-8601 format.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PdfContractError("PDF contract time must include a timezone.")
    return parsed.isoformat(timespec="seconds")


def _identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise PdfContractError(f"{label} has an invalid format.")
    return text


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _FINGERPRINT.fullmatch(text):
        raise PdfContractError(f"{label} must be a SHA-256 fingerprint.")
    return text


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise PdfContractError(f"{label} must be an integer.")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise PdfContractError(f"{label} must be an integer.") from error
    if number < minimum or number > maximum:
        raise PdfContractError(f"{label} is outside the allowed range.")
    return number


def _warning_ids(values: Any) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PdfContractError("PDF warnings must be a sequence of identifiers.")
    try:
        normalized = tuple(str(item or "").strip().casefold() for item in values)
    except TypeError as error:
        raise PdfContractError("PDF warnings must be a sequence of identifiers.") from error
    if any(not _WARNING_ID.fullmatch(item) for item in normalized):
        raise PdfContractError("PDF warnings allow content-free identifiers only.")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class PdfDocumentSnapshot:
    """Content-free identity and bounded structural metadata for one PDF."""

    document_id: str
    document_fingerprint: str
    page_count: int
    file_size_bytes: int
    encrypted: bool
    document_kind: PdfDocumentKind | str = PdfDocumentKind.UNKNOWN
    captured_at: str = field(default_factory=_timestamp)
    schema_version: int = PDF_CONTRACT_SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document ID"))
        object.__setattr__(
            self,
            "document_fingerprint",
            _fingerprint(self.document_fingerprint, "document fingerprint"),
        )
        object.__setattr__(
            self,
            "page_count",
            _bounded_integer(self.page_count, "PDF page count", 0, MAX_PDF_CONTRACT_PAGES),
        )
        object.__setattr__(
            self,
            "file_size_bytes",
            _bounded_integer(self.file_size_bytes, "PDF file size", 0, 2**63 - 1),
        )
        if type(self.encrypted) is not bool:
            raise PdfContractError("PDF encrypted state must be a boolean.")
        try:
            kind = PdfDocumentKind(self.document_kind)
        except ValueError as error:
            raise PdfContractError("Unsupported PDF document kind.") from error
        object.__setattr__(self, "document_kind", kind)
        object.__setattr__(self, "captured_at", _timestamp(self.captured_at))
        if int(self.schema_version) != PDF_CONTRACT_SCHEMA_VERSION:
            raise PdfContractError("Unsupported PDF contract schema version.")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["document_kind"] = self.document_kind.value
        return copy.deepcopy(value)

    def to_evidence_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PdfDocumentSnapshot":
        if not isinstance(value, Mapping):
            raise PdfContractError("PDF document snapshot must be an object.")
        return cls(**dict(value))


@dataclass(frozen=True)
class PdfPageText:
    """One page's transient text plus a persistable content-free projection."""

    page_number: int
    text: str
    extraction_method: PdfExtractionMethod | str
    character_count: int
    text_fingerprint: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        page_number = _bounded_integer(
            self.page_number, "PDF page number", 1, MAX_PDF_CONTRACT_PAGES
        )
        text = str(self.text or "")
        if "\x00" in text or len(text) > MAX_PAGE_TEXT_CHARS:
            raise PdfContractError("PDF page text exceeds the safe limit.")
        character_count = _bounded_integer(
            self.character_count, "PDF page character count", 0, MAX_PAGE_TEXT_CHARS
        )
        if character_count != len(text):
            raise PdfContractError("PDF page character count does not match the text.")
        expected_fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        fingerprint = _fingerprint(self.text_fingerprint, "page text fingerprint")
        if fingerprint != expected_fingerprint:
            raise PdfContractError("PDF page text fingerprint does not match the text.")
        try:
            method = PdfExtractionMethod(self.extraction_method)
        except ValueError as error:
            raise PdfContractError("Unsupported PDF extraction method.") from error
        if method is PdfExtractionMethod.NONE and text:
            raise PdfContractError("Extraction method 'none' cannot contain text.")
        object.__setattr__(self, "page_number", page_number)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "character_count", character_count)
        object.__setattr__(self, "text_fingerprint", fingerprint)
        object.__setattr__(self, "extraction_method", method)
        object.__setattr__(self, "warnings", _warning_ids(self.warnings))

    @classmethod
    def from_text(
        cls,
        page_number: int,
        text: str,
        extraction_method: PdfExtractionMethod | str = PdfExtractionMethod.PYPDF,
        warnings: tuple[str, ...] = (),
    ) -> "PdfPageText":
        normalized = str(text or "")
        return cls(
            page_number=page_number,
            text=normalized,
            extraction_method=extraction_method,
            character_count=len(normalized),
            text_fingerprint=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            warnings=warnings,
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["extraction_method"] = self.extraction_method.value
        value["warnings"] = list(self.warnings)
        return copy.deepcopy(value)

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "extraction_method": self.extraction_method.value,
            "character_count": self.character_count,
            "text_fingerprint": self.text_fingerprint,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PdfExtractionResult:
    """A page-bounded extraction result that keeps persistence content-free."""

    document: PdfDocumentSnapshot
    pages: tuple[PdfPageText, ...]
    requested_pages: tuple[int, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not isinstance(self.document, PdfDocumentSnapshot):
            raise PdfContractError("PDF extraction result needs a document snapshot.")
        try:
            pages = tuple(self.pages)
        except TypeError as error:
            raise PdfContractError("PDF extraction pages must be a sequence.") from error
        if any(not isinstance(item, PdfPageText) for item in pages):
            raise PdfContractError("PDF extraction page contract is invalid.")
        page_numbers = tuple(item.page_number for item in pages)
        if page_numbers != tuple(sorted(set(page_numbers))):
            raise PdfContractError("PDF extraction pages must be sorted and unique.")
        requested = tuple(
            _bounded_integer(number, "requested PDF page", 1, max(1, self.document.page_count))
            for number in self.requested_pages
        )
        if requested != tuple(sorted(set(requested))):
            raise PdfContractError("Requested PDF pages must be sorted and unique.")
        if page_numbers != requested:
            raise PdfContractError("Requested PDF pages do not match extracted pages.")
        if page_numbers and page_numbers[-1] > self.document.page_count:
            raise PdfContractError("An extracted page exceeds the PDF page count.")
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "requested_pages", requested)
        object.__setattr__(self, "warnings", _warning_ids(self.warnings))

    @property
    def combined_text(self) -> str:
        return "\n".join(item.text for item in self.pages if item.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "pages": [item.to_dict() for item in self.pages],
            "requested_pages": list(self.requested_pages),
            "warnings": list(self.warnings),
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_evidence_dict(),
            "pages": [item.to_evidence_dict() for item in self.pages],
            "requested_pages": list(self.requested_pages),
            "warnings": list(self.warnings),
        }
