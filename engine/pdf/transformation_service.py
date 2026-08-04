"""Approved, atomic PDF page extraction, merge, rotation, and undo."""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from engine.file_actions import (
    PageSelection,
    PreparedFileAction,
    evaluate_pdf_output_path,
    suggest_pdf_output_path,
)
from engine.pdf.errors import PdfReadError
from engine.pdf.file_picker import choose_pdf_documents
from engine.pdf.intent import PdfCommandRequest, PdfIntentKind
from engine.pdf.reader import MAX_PDF_FILE_BYTES, MAX_PDF_PAGES, read_pdf_document
from engine.pdf.reference import PdfReferenceError
from engine.pdf.transformation_verification import (
    PdfTransformationVerificationError,
    file_sha256,
    read_page_rotations,
    verify_pdf_output,
)


class PdfTransformationError(PdfReferenceError):
    """A recoverable, content-free PDF transformation failure."""


class PdfTransformationCancelled(PdfTransformationError):
    status = "cancelled"
    error_type = "user_cancelled"


def _action_contract(operation, sources, output, selections, current, expected):
    return PreparedFileAction(
        action_id=f"pdf-action-{uuid.uuid4().hex}",
        request_id=f"pdf-request-{uuid.uuid4().hex}",
        operation=operation,
        source_fingerprints=tuple(item.document.document_fingerprint for item in sources),
        output_path=output,
        page_selection=tuple(selections),
        current_state=current,
        expected_state=expected,
        destructive=False,
        reversible=True,
        requires_approval=True,
        verification_plan={"method": "pdf_page_readback"},
        rollback_plan={"strategy": "delete_owned_output_if_unchanged"},
    )


def _sequence_state(source_paths, extractions, selections, rotation_delta=None):
    fingerprints = []
    rotations = []
    order = []
    rotated_pages = set((rotation_delta or {}).get("pages", ()))
    degrees = int((rotation_delta or {}).get("degrees", 0))
    for selection in selections:
        extraction = extractions[selection.source_index]
        pages_by_number = {page.page_number: page for page in extraction.pages}
        source_rotations = read_page_rotations(
            source_paths[selection.source_index], selection.page_numbers
        )
        for page_number, rotation in zip(
            selection.page_numbers, source_rotations, strict=True
        ):
            fingerprints.append(pages_by_number[page_number].text_fingerprint)
            if selection.source_index == 0 and page_number in rotated_pages:
                rotation = (rotation + degrees) % 360
            rotations.append(rotation)
            order.append([selection.source_index, page_number])
    return {
        "page_count": len(order),
        "source_page_order": order,
        "text_fingerprints": fingerprints,
        "page_rotations": rotations,
    }


class PdfTransformationService:
    """Prepare before writing and verify every new PDF by reopening it."""

    def __init__(self, intake_manager, *, merge_picker=None, reader=None):
        self._intake = intake_manager
        self._merge_picker = merge_picker or choose_pdf_documents
        self._reader = reader or read_pdf_document
        self._lock = threading.RLock()
        self._last_output: dict | None = None

    def prepare(self, request: PdfCommandRequest) -> dict:
        if request.intent.kind is PdfIntentKind.UNDO:
            return self.prepare_undo()
        connection = self._intake.current(revalidate=True)
        if connection.connection_id != request.reference.connection_id:
            raise PdfTransformationError(
                "pdf_connection_replaced", "PDF 연결이 바뀌었습니다. 다시 요청해 주세요."
            )
        if request.intent.kind is PdfIntentKind.SPLIT:
            return self._prepare_extract(request, connection)
        if request.intent.kind is PdfIntentKind.ROTATE:
            return self._prepare_rotate(request, connection)
        if request.intent.kind is PdfIntentKind.MERGE:
            return self._prepare_merge(request, connection)
        raise PdfTransformationError(
            "pdf_transform_unsupported", "지원하지 않는 PDF 파일 작업입니다."
        )

    def _prepare_extract(self, request, connection):
        pages = request.reference.page_numbers
        if pages == tuple(range(1, connection.document.page_count + 1)):
            raise PdfTransformationError(
                "pdf_split_pages_missing",
                "새 PDF로 저장할 페이지 범위를 알려주세요. 예: 2~5페이지만 분할해줘.",
            )
        extraction = self._intake.read_current(pages=pages)
        selection = PageSelection(0, pages)
        return self._prepared_payload(
            request,
            connection,
            "extract_pages",
            (connection.file_path,),
            (extraction,),
            (selection,),
            "분할",
        )

    def _prepare_rotate(self, request, connection):
        degrees = request.intent.rotation_degrees
        if degrees is None:
            raise PdfTransformationError(
                "pdf_rotation_angle_missing",
                "회전 방향을 알려주세요. 예: 3페이지를 오른쪽으로 회전해줘.",
            )
        extraction = self._intake.read_current()
        target = PageSelection(0, request.reference.page_numbers)
        output_pages = PageSelection(0, tuple(range(1, connection.document.page_count + 1)))
        return self._prepared_payload(
            request,
            connection,
            "rotate_pages",
            (connection.file_path,),
            (extraction,),
            (target,),
            "회전",
            output_selections=(output_pages,),
            rotation_delta={"pages": list(target.page_numbers), "degrees": degrees},
        )

    def _prepare_merge(self, request, connection):
        selected = self._merge_picker()
        if not selected:
            raise PdfTransformationCancelled(
                "pdf_merge_selection_cancelled", "추가 PDF 선택을 취소했습니다."
            )
        source_paths = self._merge_source_paths(connection.file_path, selected)
        first = self._intake.read_current(pages=request.reference.page_numbers)
        extractions = [first]
        selections = [PageSelection(0, request.reference.page_numbers)]
        for index, path in enumerate(source_paths[1:], start=1):
            extraction = self._reader(path)
            extractions.append(extraction)
            selections.append(
                PageSelection(index, tuple(range(1, extraction.document.page_count + 1)))
            )
        return self._prepared_payload(
            request,
            connection,
            "merge_documents",
            source_paths,
            tuple(extractions),
            tuple(selections),
            "병합",
        )

    @staticmethod
    def _merge_source_paths(first_path, selected):
        paths = [str(Path(first_path).resolve(strict=True))]
        for raw_path in selected:
            path = Path(raw_path)
            if path.is_symlink():
                raise PdfTransformationError(
                    "pdf_merge_source_unsafe", "심볼릭 링크 PDF는 병합할 수 없습니다."
                )
            try:
                paths.append(str(path.resolve(strict=True)))
            except OSError as error:
                raise PdfTransformationError(
                    "pdf_merge_source_missing", "선택한 추가 PDF를 다시 찾을 수 없습니다."
                ) from error
        keys = [os.path.normcase(path) for path in paths]
        if len(keys) != len(set(keys)):
            raise PdfTransformationError(
                "pdf_merge_source_duplicate", "같은 PDF를 병합 목록에 두 번 넣을 수 없습니다."
            )
        return tuple(paths)

    def _prepared_payload(
        self,
        request,
        connection,
        operation,
        source_paths,
        extractions,
        selections,
        suffix,
        *,
        output_selections=None,
        rotation_delta=None,
    ):
        output = suggest_pdf_output_path(source_paths[0], suffix)
        evaluate_pdf_output_path(source_paths, output)
        sequence = tuple(output_selections or selections)
        try:
            expected = _sequence_state(
                source_paths, extractions, sequence, rotation_delta=rotation_delta
            )
        except Exception as error:
            raise PdfTransformationError(
                "pdf_source_inspection_failed",
                "PDF 페이지 구조를 안전하게 확인하지 못했습니다.",
            ) from error
        if expected["page_count"] > MAX_PDF_PAGES:
            raise PdfTransformationError(
                "pdf_output_page_limit",
                "예상 PDF 페이지 수가 안전 제한을 초과했습니다.",
            )
        if (
            operation == "merge_documents"
            and sum(item.document.file_size_bytes for item in extractions)
            > MAX_PDF_FILE_BYTES
        ):
            raise PdfTransformationError(
                "pdf_output_size_limit",
                "병합할 PDF의 전체 크기가 안전 제한을 초과했습니다.",
            )
        current = {
            "source_page_counts": [item.document.page_count for item in extractions],
            "output_exists": False,
            "rotation_delta": rotation_delta or {},
        }
        action = _action_contract(
            operation, extractions, output, selections, current, expected
        )
        return {
            "kind": "prepared_pdf_file_action",
            "connection_id": connection.connection_id,
            "source_paths": list(source_paths),
            "source_names": [Path(path).name for path in source_paths],
            "output_name": Path(output).name,
            "prepared_action": action.to_dict(),
        }

    def execute(self, payload: dict) -> dict:
        if payload.get("kind") == "prepared_pdf_file_undo":
            return self.execute_undo(payload)
        action = PreparedFileAction.from_dict(payload.get("prepared_action") or {})
        source_paths = tuple(payload.get("source_paths") or ())
        self._validate_prepared(action, source_paths, payload.get("connection_id"))
        temp_path = self._temp_output(action.output_path)
        written_fingerprint = None
        committed = False
        try:
            self._write_action(action, source_paths, temp_path)
            verify_pdf_output(temp_path, action.expected_state)
            written_fingerprint = file_sha256(temp_path)
            self._validate_prepared(action, source_paths, payload.get("connection_id"))
            if Path(action.output_path).exists():
                raise PdfTransformationError(
                    "pdf_output_conflict", "승인 후 같은 이름의 파일이 생겼습니다. 다시 요청해 주세요."
                )
            os.replace(temp_path, action.output_path)
            committed = True
            verified = verify_pdf_output(action.output_path, action.expected_state)
        except (PdfTransformationError, PdfReadError):
            self._cleanup_failed_output(
                temp_path, action.output_path, written_fingerprint, committed
            )
            raise
        except PdfTransformationVerificationError as error:
            self._cleanup_failed_output(
                temp_path, action.output_path, written_fingerprint, committed
            )
            raise PdfTransformationError(
                "pdf_output_verification_failed",
                "새 PDF를 다시 읽어 검증하지 못해 결과 파일을 정리했습니다.",
            ) from error
        except Exception as error:
            self._cleanup_failed_output(
                temp_path, action.output_path, written_fingerprint, committed
            )
            raise PdfTransformationError(
                "pdf_transform_execution_failed",
                "PDF 파일 작업을 완료하지 못해 불완전한 결과를 정리했습니다.",
            ) from error
        self._remember_output(action, written_fingerprint)
        return {
            "file_action": action.to_evidence_dict(),
            "output_name": Path(action.output_path).name,
            "output_fingerprint": written_fingerprint,
            "verification": verified,
            "source_files_unchanged": True,
            "rollback_available": True,
        }

    def _validate_prepared(self, action, source_paths, connection_id):
        if len(source_paths) != len(action.source_fingerprints):
            raise PdfTransformationError(
                "pdf_confirmation_invalid", "PDF 승인 정보가 올바르지 않습니다."
            )
        connection = self._intake.current(revalidate=True)
        if connection.connection_id != connection_id:
            raise PdfTransformationError(
                "pdf_context_changed", "승인 대기 중 PDF 연결이 바뀌었습니다."
            )
        if os.path.normcase(connection.file_path) != os.path.normcase(source_paths[0]):
            raise PdfTransformationError(
                "pdf_context_changed", "승인 대기 중 PDF 대상이 바뀌었습니다."
            )
        for path, fingerprint in zip(
            source_paths, action.source_fingerprints, strict=True
        ):
            if self._reader(path, pages=1).document.document_fingerprint != fingerprint:
                raise PdfTransformationError(
                    "pdf_source_changed", "승인 대기 중 원본 PDF가 변경되었습니다."
                )
        evaluate_pdf_output_path(source_paths, action.output_path)

    @staticmethod
    def _temp_output(output_path):
        descriptor, path = tempfile.mkstemp(
            prefix=".jarvis_pdf_", suffix=".pdf", dir=str(Path(output_path).parent)
        )
        os.close(descriptor)
        return path

    @staticmethod
    def _write_action(action, source_paths, temp_path):
        readers = [PdfReader(path, strict=True) for path in source_paths]
        writer = PdfWriter()
        if action.operation in {"extract_pages", "merge_documents"}:
            for selection in action.page_selection:
                for page_number in selection.page_numbers:
                    writer.add_page(readers[selection.source_index].pages[page_number - 1])
        else:
            targets = set(action.page_selection[0].page_numbers)
            degrees = int(action.current_state["rotation_delta"]["degrees"])
            for page_number, page in enumerate(readers[0].pages, start=1):
                if page_number in targets:
                    page.rotate(degrees)
                writer.add_page(page)
        with Path(temp_path).open("wb") as stream:
            writer.write(stream)

    @staticmethod
    def _cleanup_failed_output(temp_path, output_path, fingerprint, committed):
        Path(temp_path).unlink(missing_ok=True)
        output = Path(output_path)
        if committed and fingerprint and output.exists():
            try:
                unchanged = file_sha256(output) == fingerprint
            except OSError:
                unchanged = False
            if unchanged:
                output.unlink(missing_ok=True)

    def _remember_output(self, action, fingerprint):
        with self._lock:
            self._last_output = {
                "action_id": action.action_id,
                "output_path": action.output_path,
                "output_name": Path(action.output_path).name,
                "output_fingerprint": fingerprint,
            }

    def prepare_undo(self) -> dict:
        with self._lock:
            receipt = dict(self._last_output or {})
        output = Path(str(receipt.get("output_path") or ""))
        if not receipt or not output.is_file():
            raise PdfTransformationError(
                "pdf_undo_missing", "이번 실행에서 되돌릴 PDF 결과물이 없습니다."
            )
        try:
            unchanged = file_sha256(output) == receipt.get("output_fingerprint")
        except OSError as error:
            raise PdfTransformationError(
                "pdf_undo_read_failed", "PDF 결과물 상태를 확인하지 못했습니다."
            ) from error
        if not unchanged:
            raise PdfTransformationError(
                "pdf_undo_output_changed",
                "생성한 PDF가 이후 변경되어 안전하게 삭제할 수 없습니다.",
            )
        return {"kind": "prepared_pdf_file_undo", **receipt}

    def execute_undo(self, payload: dict) -> dict:
        output = Path(str(payload.get("output_path") or ""))
        expected = str(payload.get("output_fingerprint") or "")
        try:
            unchanged = output.is_file() and file_sha256(output) == expected
        except OSError as error:
            raise PdfTransformationError(
                "pdf_undo_read_failed", "승인한 PDF 결과물 상태를 확인하지 못했습니다."
            ) from error
        if not unchanged:
            raise PdfTransformationError(
                "pdf_undo_output_changed",
                "승인 대기 중 PDF 결과물이 변경되어 삭제하지 않았습니다.",
            )
        output.unlink()
        if output.exists():
            raise PdfTransformationError(
                "pdf_undo_failed", "PDF 결과물 삭제를 확인하지 못했습니다."
            )
        with self._lock:
            if (self._last_output or {}).get("action_id") == payload.get("action_id"):
                self._last_output = None
        return {
            "output_name": str(payload.get("output_name") or ""),
            "deleted": True,
            "output_fingerprint": expected,
        }


__all__ = [
    "PdfTransformationCancelled",
    "PdfTransformationError",
    "PdfTransformationService",
]
