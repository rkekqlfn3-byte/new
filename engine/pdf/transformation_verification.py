"""Content-free read-back checks for newly created PDF transformations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from pypdf import PdfReader

from engine.pdf.reader import read_pdf_document


class PdfTransformationVerificationError(RuntimeError):
    """A transformed PDF did not match its approved expected state."""


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_page_rotations(path: str | Path, page_numbers) -> tuple[int, ...]:
    reader = PdfReader(str(path), strict=True)
    rotations = []
    for page_number in page_numbers:
        rotation = int(reader.pages[int(page_number) - 1].rotation or 0) % 360
        if rotation not in {0, 90, 180, 270}:
            raise PdfTransformationVerificationError("PDF page rotation is unsupported.")
        rotations.append(rotation)
    return tuple(rotations)


def verify_pdf_output(path: str | Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    output = Path(path)
    with output.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise PdfTransformationVerificationError("PDF output header is invalid.")
    extraction = read_pdf_document(output)
    expected_count = int(expected.get("page_count", -1))
    if extraction.document.page_count != expected_count:
        raise PdfTransformationVerificationError("PDF output page count does not match.")
    actual_fingerprints = tuple(page.text_fingerprint for page in extraction.pages)
    expected_fingerprints = tuple(expected.get("text_fingerprints") or ())
    if actual_fingerprints != expected_fingerprints:
        raise PdfTransformationVerificationError("PDF output page order does not match.")
    rotations = read_page_rotations(output, range(1, expected_count + 1))
    if rotations != tuple(expected.get("page_rotations") or ()):
        raise PdfTransformationVerificationError("PDF output rotations do not match.")
    return {
        "header_verified": True,
        "page_count": expected_count,
        "page_order_verified": True,
        "page_rotations_verified": True,
        "document_fingerprint": extraction.document.document_fingerprint,
    }


__all__ = [
    "PdfTransformationVerificationError",
    "file_sha256",
    "read_page_rotations",
    "verify_pdf_output",
]
