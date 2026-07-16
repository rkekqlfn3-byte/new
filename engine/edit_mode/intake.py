"""Validate local document handoff and confirm the matching native document."""

from __future__ import annotations

from pathlib import Path

from engine.edit_mode.native_bridge import NativeBridgeError, NativeDocumentBridge
from engine.edit_mode.session import canonical_document_path


SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xlsb": "excel",
    ".xls": "excel",
    ".hwp": "hwp",
    ".hwpx": "hwp",
    ".docx": "word",
    ".docm": "word",
    ".doc": "word",
    ".pptx": "powerpoint",
    ".pptm": "powerpoint",
    ".ppt": "powerpoint",
}

APP_LABELS = {
    "excel": "Excel",
    "hwp": "한글",
    "word": "Word",
    "powerpoint": "PowerPoint",
}


class EditIntakeError(RuntimeError):
    error_type = "validation_error"
    status = "blocked"
    candidates: tuple[dict, ...] = ()


class UnsupportedEditDocument(EditIntakeError):
    pass


class EditAppUnavailable(EditIntakeError):
    error_type = "environment_error"


class EditDocumentOpenTimeout(EditIntakeError):
    error_type = "target_not_found"
    status = "failed"


class EditDocumentAmbiguous(EditIntakeError):
    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.candidates = tuple(dict(item) for item in candidates)


def app_type_for_path(file_path: str) -> str:
    suffix = Path(str(file_path or "")).suffix.casefold()
    app_type = SUPPORTED_DOCUMENT_EXTENSIONS.get(suffix)
    if not app_type:
        supported = " ".join(sorted(SUPPORTED_DOCUMENT_EXTENSIONS))
        raise UnsupportedEditDocument(
            f"지원하지 않는 문서 형식입니다. 지원 확장자: {supported}"
        )
    return app_type


class FileIntakeManager:
    """Open one supported local file and verify the exact native document path."""

    def __init__(self, bridge=None, open_timeout=15.0):
        self.bridge = bridge or NativeDocumentBridge()
        self.open_timeout = max(1.0, min(float(open_timeout), 30.0))

    def connect_file(self, file_path: str) -> dict:
        canonical_path = canonical_document_path(file_path)
        app_type = app_type_for_path(canonical_path)
        if not self.bridge.is_available(app_type):
            raise EditAppUnavailable(
                f"{APP_LABELS[app_type]}이 설치되어 있지 않아 문서를 열 수 없습니다."
            )

        document = self.bridge.find_document(app_type, canonical_path)
        launch_requested = document is None
        if launch_requested:
            try:
                self.bridge.launch_document(app_type, canonical_path)
            except NativeBridgeError as error:
                raise EditAppUnavailable(str(error)) from error
            document = self.bridge.wait_for_document(
                app_type,
                canonical_path,
                timeout=self.open_timeout,
            )
        if not document:
            raise EditDocumentOpenTimeout(
                f"{APP_LABELS[app_type]}에서 선택한 파일이 열린 것을 확인하지 못했습니다. "
                "앱의 경고 또는 보호된 보기 창을 확인해주세요."
            )
        verified_path = canonical_document_path(document.get("file_path"))
        if verified_path != canonical_path:
            raise EditDocumentOpenTimeout(
                "열린 문서 경로가 선택한 파일과 일치하지 않아 연결하지 않았습니다."
            )
        result = dict(document)
        result.update({
            "app_type": app_type,
            "file_path": canonical_path,
            "document_name": str(
                result.get("document_name") or Path(canonical_path).name
            ),
            "launch_requested": launch_requested,
        })
        return result

    def connect_active_document(self, app_type: str | None = None) -> dict:
        requested = str(app_type or "").strip().casefold()
        if requested and requested not in APP_LABELS:
            raise EditIntakeError("현재 문서 연결 앱 유형이 올바르지 않습니다.")
        documents = self.bridge.active_documents(requested or None)
        supported = []
        for document in documents:
            try:
                path = canonical_document_path(document.get("file_path"))
                expected_app = app_type_for_path(path)
            except EditIntakeError:
                continue
            except Exception:
                continue
            if expected_app != str(document.get("app_type") or "").casefold():
                continue
            item = dict(document)
            item["file_path"] = path
            item["launch_requested"] = False
            supported.append(item)

        if not supported:
            label = APP_LABELS.get(requested, "Office 또는 한글")
            raise EditDocumentOpenTimeout(
                f"저장된 {label} 문서를 찾지 못했습니다. 문서를 연 뒤 다시 시도해주세요."
            )
        if len(supported) > 1:
            candidates = tuple({
                "app_type": item["app_type"],
                "document_name": item.get("document_name") or Path(
                    item["file_path"]
                ).name,
            } for item in supported)
            raise EditDocumentAmbiguous(
                "연결 가능한 문서가 여러 개입니다. 앱을 지정하거나 원하는 문서만 활성화해주세요.",
                candidates,
            )
        return supported[0]

    def connect_dropped_document(
        self,
        *,
        file_name: str,
        file_size=None,
        path_hint: str | None = None,
    ) -> dict:
        """Resolve a browser drop without copying or uploading file contents."""
        candidate = str(path_hint or "").strip()
        if candidate and Path(candidate).name.casefold() == str(file_name).casefold():
            return self.connect_file(candidate)
        resolved = self.bridge.resolve_explorer_selection(file_name, file_size)
        if not resolved:
            raise EditIntakeError(
                "브라우저 보안 때문에 드롭한 파일의 로컬 경로를 확인하지 못했습니다. "
                "'파일 선택' 버튼을 사용해주세요."
            )
        return self.connect_file(resolved)
