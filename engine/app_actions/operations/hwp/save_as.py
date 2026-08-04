"""Export the active Hanword document to a new PDF file."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.hwp.base import HwpOperation


def file_snapshot(path):
    if not os.path.exists(path):
        return {"exists": False}
    if not os.path.isfile(path):
        raise AppActionBlocked("저장 대상 경로가 일반 파일이 아닙니다.")
    stat = os.stat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "exists": True,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest.hexdigest().upper(),
    }


def verify_pdf(path):
    if not os.path.isfile(path) or os.path.getsize(path) < 100:
        raise AppActionVerificationError("생성된 PDF 파일이 없거나 비어 있습니다.")
    with open(path, "rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise AppActionVerificationError("생성된 파일이 올바른 PDF 형식이 아닙니다.")
    try:
        from pypdf import PdfReader

        page_count = len(PdfReader(path).pages)
    except Exception as error:
        raise AppActionVerificationError("생성된 PDF를 다시 열어 검증하지 못했습니다.") from error
    if page_count < 1:
        raise AppActionVerificationError("생성된 PDF에 페이지가 없습니다.")
    return {"size": os.path.getsize(path), "page_count": page_count}


class SaveAsOperation(HwpOperation):
    name = "save_as"

    @staticmethod
    def _resolve_pdf_target(base, params):
        requested = str(params.get("path") or params.get("target_path") or "").strip()
        if requested:
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(requested)))
        else:
            source = base["full_name"]
            if not source:
                raise AppActionBlocked(
                    "저장되지 않은 한글 문서는 PDF 대상 경로를 직접 지정해주세요."
                )
            path = os.path.splitext(source)[0] + ".pdf"
        if os.path.splitext(path)[1].casefold() != ".pdf":
            raise AppActionBlocked("7단계 다른 이름 저장은 PDF 형식만 지원합니다.")
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise AppActionBlocked("PDF를 저장할 폴더가 존재하지 않습니다.")
        return path

    def prepare(self, adapter, hwp, params):
        if not adapter._enable_pdf_export:
            raise AppActionBlocked(
                "현재 한글 2024의 PDF Automation이 간헐적으로 응답을 멈춰 안전상 자동 저장을 차단했습니다. 한글의 PDF 저장 기능을 직접 사용해주세요."
            )
        _, base, _, selection = adapter._context(hwp)
        requested_format = str(params.get("format") or "PDF").strip().upper()
        if requested_format != "PDF":
            raise AppActionBlocked("7단계 다른 이름 저장은 PDF 형식만 지원합니다.")
        path = self._resolve_pdf_target(base, params)
        target_state = file_snapshot(path)
        snapshot = {
            **base,
            "operation": self.name,
            "target": path,
            "format": "PDF",
            "target_state": target_state,
        }
        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["document_name"],
            sheet="현재 문서",
            target=path,
            params={"path": path, "format": "PDF"},
            current_state={
                "target_exists": target_state["exists"],
                "document_modified": base["is_modified"],
                "document_text_digest": base["text_digest"],
                "has_selection": selection["has_selection"],
            },
            estimated_changes=1,
            destructive=bool(target_state["exists"]),
            reversible=False,
            verification_method="verify_pdf_header_and_pages",
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            metadata={"window_handle": base["window_handle"]},
        )

    def run(self, adapter, hwp, current):
        target = current.params["path"]
        parent = os.path.dirname(target)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".jarvis-hwp-", suffix=".pdf", dir=parent
        )
        os.close(descriptor)
        os.unlink(temp_path)
        backup_path = None
        committed = False
        try:
            action = hwp.CreateAction("PrintToPDFEx")
            parameter_set = action.CreateSet()
            action.GetDefault(parameter_set)
            parameter_set.SetItem("FileName", temp_path)
            parameter_set.SetItem("Range", 6)
            action.Execute(parameter_set)
            pdf_state = verify_pdf(temp_path)
            if os.path.exists(target):
                descriptor, backup_path = tempfile.mkstemp(
                    prefix=".jarvis-hwp-backup-", suffix=".pdf", dir=parent
                )
                os.close(descriptor)
                shutil.copy2(target, backup_path)
            os.replace(temp_path, target)
            committed = True
            final_state = verify_pdf(target)
            _, after_base, _, _ = adapter._context(hwp)
            if (
                after_base["document_id"] != current.document_id
                or after_base["text_digest"]
                != current.current_state["document_text_digest"]
            ):
                raise AppActionVerificationError(
                    "PDF 저장 중 활성 한글 문서 또는 문서 내용이 바뀌었습니다."
                )
        except Exception as error:
            if committed:
                try:
                    if backup_path and os.path.exists(backup_path):
                        os.replace(backup_path, target)
                        backup_path = None
                    elif os.path.exists(target):
                        os.unlink(target)
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "한글 PDF 저장 또는 검증에 실패했습니다."
            ) from error
        finally:
            for path in (temp_path, backup_path):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
        return adapter._result(
            current,
            {"target_exists": current.current_state["target_exists"]},
            {"path": target, **final_state, "temporary_pdf": pdf_state},
            True,
        )


__all__ = ["SaveAsOperation", "file_snapshot", "verify_pdf"]
