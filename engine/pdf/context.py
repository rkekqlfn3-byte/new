"""Runtime-only connected PDF identity with a content-free public projection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.pdf.contracts import PdfDocumentSnapshot

PDF_CONNECTION_SCHEMA_VERSION = 1
_CONNECTION_ID = re.compile(r"^pdf-connection-[a-f0-9]{32}$")


@dataclass(frozen=True)
class PdfConnection:
    connection_id: str
    file_path: str
    document_name: str
    document: PdfDocumentSnapshot
    connected_at: str
    schema_version: int = PDF_CONNECTION_SCHEMA_VERSION

    def __post_init__(self):
        if not _CONNECTION_ID.fullmatch(str(self.connection_id or "")):
            raise ValueError("PDF connection ID is invalid.")
        path = os.path.abspath(str(self.file_path or ""))
        if not os.path.isabs(path) or Path(path).suffix.casefold() != ".pdf":
            raise ValueError("PDF connection path is invalid.")
        name = str(self.document_name or "").strip()
        if not name or any(ord(character) < 32 for character in name) or len(name) > 260:
            raise ValueError("PDF connection display name is invalid.")
        if not isinstance(self.document, PdfDocumentSnapshot):
            raise ValueError("PDF connection requires a document snapshot.")
        if int(self.schema_version) != PDF_CONNECTION_SCHEMA_VERSION:
            raise ValueError("Unsupported PDF connection schema version.")
        object.__setattr__(self, "file_path", path)
        object.__setattr__(self, "document_name", name)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connection_id": self.connection_id,
            "document_name": self.document_name,
            "document_fingerprint": self.document.document_fingerprint,
            "page_count": self.document.page_count,
            "file_size_bytes": self.document.file_size_bytes,
            "document_kind": self.document.document_kind.value,
            "read_only": True,
            "connected_at": self.connected_at,
        }

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connection_id": self.connection_id,
            "document_fingerprint": self.document.document_fingerprint,
            "page_count": self.document.page_count,
            "file_size_bytes": self.document.file_size_bytes,
            "document_kind": self.document.document_kind.value,
            "read_only": True,
            "connected_at": self.connected_at,
        }
