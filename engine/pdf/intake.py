"""Process-local PDF connection manager kept separate from Office edit intake."""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from engine.pdf.context import PdfConnection
from engine.pdf.errors import PdfReadError, PdfReadErrorCode
from engine.pdf.file_picker import choose_pdf_document
from engine.pdf.intent import PdfCommandRequest, parse_pdf_intent
from engine.pdf.reader import read_pdf_document
from engine.pdf.reference import (
    PdfReferenceError,
    PdfTargetReference,
    resolve_pdf_reference,
)
from engine.pdf.search import PdfSearchResult, search_pdf_text


class PdfConnectionError(PdfReferenceError):
    pass


class PdfIntakeManager:
    """Own one explicit read-only PDF connection for the current process."""

    def __init__(self, *, reader=None, file_picker=None):
        self._reader = reader or read_pdf_document
        self._file_picker = file_picker or choose_pdf_document
        self._lock = threading.RLock()
        self._connection: PdfConnection | None = None
        self._last_search: PdfSearchResult | None = None
        self._last_reference: PdfTargetReference | None = None

    @staticmethod
    def _new_connection(path: Path, extraction) -> PdfConnection:
        return PdfConnection(
            connection_id=f"pdf-connection-{uuid.uuid4().hex}",
            file_path=str(path),
            document_name=path.name,
            document=extraction.document,
            connected_at=extraction.document.captured_at,
        )

    def connect_file(self, file_path: str) -> dict:
        extraction = self._reader(file_path, pages=1)
        expanded = os.path.expandvars(os.path.expanduser(str(file_path or "")))
        path = Path(expanded).resolve(strict=True)
        connection = self._new_connection(path, extraction)
        with self._lock:
            self._connection = connection
            self._last_search = None
            self._last_reference = None
        return connection.to_public_dict()

    def choose_and_connect(self) -> dict | None:
        selected = self._file_picker()
        return self.connect_file(selected) if selected else None

    def connect_dropped(
        self,
        file_name: str,
        file_size: int | None = None,
        path_hint: str | None = None,
    ) -> dict:
        name = str(file_name or "").strip()
        if (
            not name
            or Path(name).name != name
            or Path(name).suffix.casefold() != ".pdf"
            or any(ord(character) < 32 for character in name)
            or len(name) > 260
        ):
            raise PdfConnectionError(
                "pdf_drop_invalid",
                "한 번에 올바른 PDF 파일 하나만 놓아주세요.",
            )
        if not path_hint:
            raise PdfConnectionError(
                "pdf_drop_path_unavailable",
                "이 브라우저에서는 놓은 파일의 정확한 경로를 확인할 수 없습니다. "
                "'PDF 연결' 버튼으로 파일을 선택해주세요.",
            )
        expanded = os.path.expandvars(os.path.expanduser(str(path_hint)))
        candidate = Path(expanded)
        if candidate.is_symlink():
            raise PdfConnectionError(
                "pdf_drop_path_invalid",
                "심볼릭 링크로 놓은 PDF는 연결할 수 없습니다.",
            )
        try:
            path = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as error:
            raise PdfConnectionError(
                "pdf_drop_path_invalid",
                "놓은 PDF의 정확한 파일 위치를 확인하지 못했습니다.",
            ) from error
        if path.name.casefold() != name.casefold():
            raise PdfConnectionError(
                "pdf_drop_identity_mismatch",
                "놓은 PDF의 이름과 파일 위치가 일치하지 않습니다.",
            )
        if file_size is not None:
            if type(file_size) is not int or file_size < 0:
                raise PdfConnectionError(
                    "pdf_drop_size_invalid",
                    "놓은 PDF의 파일 크기 정보가 올바르지 않습니다.",
                )
            try:
                actual_size = path.stat().st_size
            except OSError as error:
                raise PdfConnectionError(
                    "pdf_drop_path_invalid",
                    "놓은 PDF의 파일 정보를 확인하지 못했습니다.",
                ) from error
            if actual_size != file_size:
                raise PdfConnectionError(
                    "pdf_drop_identity_mismatch",
                    "놓은 PDF의 파일 크기와 실제 파일이 일치하지 않습니다.",
                )
        return self.connect_file(str(path))

    def status(self, *, revalidate: bool = False) -> dict:
        with self._lock:
            connection = self._connection
        if connection is not None and revalidate:
            connection = self._revalidate(connection)
        return {
            "connected": connection is not None,
            "connection": connection.to_public_dict() if connection else None,
        }

    def current(self, *, revalidate: bool = False) -> PdfConnection:
        with self._lock:
            connection = self._connection
        if connection is None:
            raise PdfConnectionError(
                "pdf_not_connected",
                "먼저 읽을 PDF를 연결해주세요.",
            )
        return self._revalidate(connection) if revalidate else connection

    def _clear_if_current(self, connection_id: str):
        with self._lock:
            if self._connection and self._connection.connection_id == connection_id:
                self._connection = None
                self._last_search = None
                self._last_reference = None

    def _revalidate(self, connection: PdfConnection) -> PdfConnection:
        try:
            extraction = self._reader(connection.file_path, pages=1)
        except PdfReadError:
            self._clear_if_current(connection.connection_id)
            raise
        current = extraction.document
        expected = connection.document
        if (
            current.document_fingerprint != expected.document_fingerprint
            or current.page_count != expected.page_count
            or current.file_size_bytes != expected.file_size_bytes
        ):
            self._clear_if_current(connection.connection_id)
            raise PdfReadError(
                PdfReadErrorCode.SOURCE_CHANGED,
                "Connected PDF changed and must be connected again.",
            )
        with self._lock:
            active = self._connection
            if active is None or active.connection_id != connection.connection_id:
                raise PdfConnectionError(
                    "pdf_connection_replaced",
                    "PDF를 확인하는 동안 연결 대상이 바뀌었습니다. 다시 요청해주세요.",
                )
        return connection

    def disconnect(self, connection_id: str | None = None) -> dict:
        with self._lock:
            connection = self._connection
            if connection is None:
                raise PdfConnectionError(
                    "pdf_not_connected",
                    "현재 연결된 PDF가 없습니다.",
                )
            if connection_id and connection.connection_id != str(connection_id):
                raise PdfConnectionError(
                    "pdf_connection_mismatch",
                    "현재 PDF 연결과 다른 연결 해제 요청입니다.",
                )
            self._connection = None
            self._last_search = None
            self._last_reference = None
        return connection.to_public_dict()

    def read_current(self, pages=None):
        connection = self.current(revalidate=True)
        extraction = self._reader(connection.file_path, pages=pages)
        if extraction.document.document_fingerprint != connection.document.document_fingerprint:
            self._clear_if_current(connection.connection_id)
            raise PdfReadError(
                PdfReadErrorCode.SOURCE_CHANGED,
                "Connected PDF changed during reading.",
            )
        return extraction

    def search_current(self, query: str, *, pages=None) -> PdfSearchResult:
        extraction = self.read_current(pages=pages)
        result = search_pdf_text(extraction, query)
        with self._lock:
            active = self._connection
            if (
                active is None
                or active.document.document_fingerprint != result.document.document_fingerprint
            ):
                raise PdfConnectionError(
                    "pdf_connection_replaced",
                    "PDF 검색 중 연결 대상이 바뀌었습니다. 다시 검색해주세요.",
                )
            self._last_search = result
            hit_pages = tuple(sorted({match.page_number for match in result.matches}))
            self._last_reference = (
                PdfTargetReference(
                    connection_id=active.connection_id,
                    document_fingerprint=active.document.document_fingerprint,
                    page_numbers=hit_pages,
                    source="last_search",
                )
                if hit_pages
                else None
            )
        return result

    def resolve_reference(self, command: str) -> PdfTargetReference:
        connection = self.current(revalidate=True)
        with self._lock:
            last_search = self._last_search
            last_reference = self._last_reference
        reference = resolve_pdf_reference(
            command,
            connection,
            last_search=last_search,
            last_reference=last_reference,
        )
        with self._lock:
            active = self._connection
            if active is None or active.connection_id != connection.connection_id:
                raise PdfConnectionError(
                    "pdf_connection_replaced",
                    "PDF 참조를 확인하는 동안 연결 대상이 바뀌었습니다. 다시 요청해주세요.",
                )
            self._last_reference = reference
        return reference

    def resolve_command(self, command: str) -> PdfCommandRequest:
        intent = parse_pdf_intent(command)
        reference = self.resolve_reference(command)
        return PdfCommandRequest(intent=intent, reference=reference)
