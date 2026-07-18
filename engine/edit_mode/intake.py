"""Validate local document handoff and confirm the matching native document."""

from __future__ import annotations

from pathlib import Path

from engine.edit_mode.native_bridge import (
    NativeBridgeError,
    NativeDocumentBridge,
    NativeOfficeBusy,
)
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


class EditAppBusy(EditIntakeError):
    error_type = "environment_error"
    status = "busy"
    retryable = True


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

    def __init__(self, bridge=None, open_timeout=15.0, busy_timeout=2.0):
        self.bridge = bridge or NativeDocumentBridge()
        self.open_timeout = max(1.0, min(float(open_timeout), 30.0))
        self.busy_timeout = max(0.5, min(float(busy_timeout), 5.0))

    @staticmethod
    def _busy_error(app_type: str, error) -> EditAppBusy:
        label = APP_LABELS.get(app_type, "Office")
        return EditAppBusy(
            f"{label}이 셀·문서 입력 중이거나 대화상자를 처리 중이라 "
            "문서 연결을 잠시 거부했습니다. 입력을 마친 뒤 다시 시도해주세요."
        )

    def _runtime_excel_document(self, document: dict) -> dict:
        item = dict(document or {})
        handle = int(item.get("window_handle") or 0)
        name = str(item.get("document_name") or "현재 통합문서").strip()
        if handle <= 0:
            raise EditDocumentOpenTimeout(
                "현재 미저장 Excel 창을 안전하게 식별하지 못했습니다. "
                "통합문서 창을 다시 선택한 뒤 연결해주세요."
            )
        runtime_id = ""
        bind_runtime = getattr(self.bridge, "bind_runtime_excel_document", None)
        if callable(bind_runtime):
            runtime_id = str(bind_runtime(item) or "").strip().upper()
        if not runtime_id:
            try:
                from engine.edit_mode.native_bridge import (
                    bind_excel_runtime_window,
                )

                runtime_id = str(
                    bind_excel_runtime_window(handle) or ""
                ).strip().upper()
            except Exception:
                runtime_id = ""
        if not runtime_id:
            runtime_id = str(item.get("runtime_document_id") or "").strip().upper()
        if not runtime_id:
            runtime_id = f"EXCEL-WINDOW:{handle}:{name.casefold()}"
        item.update({
            "app_type": "excel",
            "file_path": "",
            "document_name": name,
            "runtime_document_id": runtime_id,
            "identity_kind": "runtime",
            "is_saved": False,
            "launch_requested": False,
        })
        return item

    def connect_file(self, file_path: str) -> dict:
        canonical_path = canonical_document_path(file_path)
        app_type = app_type_for_path(canonical_path)
        if not self.bridge.is_available(app_type):
            raise EditAppUnavailable(
                f"{APP_LABELS[app_type]}이 설치되어 있지 않아 문서를 열 수 없습니다."
            )

        try:
            document = self.bridge.find_document(app_type, canonical_path)
        except NativeOfficeBusy as error:
            # Do not launch the path again while Office is busy: the exact file
            # may already be open and a duplicate launch can create a modal.
            try:
                document = self.bridge.wait_for_document(
                    app_type,
                    canonical_path,
                    timeout=self.busy_timeout,
                )
            except NativeOfficeBusy as retry_error:
                raise self._busy_error(app_type, retry_error) from retry_error
        launch_requested = document is None
        if launch_requested:
            try:
                self.bridge.launch_document(app_type, canonical_path)
            except NativeBridgeError as error:
                raise EditAppUnavailable(str(error)) from error
            try:
                document = self.bridge.wait_for_document(
                    app_type,
                    canonical_path,
                    timeout=self.open_timeout,
                )
            except NativeOfficeBusy as error:
                raise self._busy_error(app_type, error) from error
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
        try:
            documents = self.bridge.active_documents(requested or None)
        except NativeOfficeBusy as error:
            raise self._busy_error(requested, error) from error
        ranked = [
            document for document in documents
            if isinstance(document.get("window_rank"), int)
        ]
        preferred = min(ranked, key=lambda item: item["window_rank"]) if ranked else None
        if preferred is not None and not preferred.get("is_saved", True):
            if str(preferred.get("app_type") or requested).casefold() == "excel":
                return self._runtime_excel_document(preferred)
            name = str(preferred.get("document_name") or "현재 문서")
            raise EditDocumentOpenTimeout(
                f"현재 전면의 문서 '{name}'은 아직 저장되지 않았습니다. "
                "먼저 저장한 뒤 다시 연결해주세요."
            )
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
            unsaved = [
                item for item in documents
                if item.get("is_saved") is False
            ]
            if unsaved:
                candidate = unsaved[0]
                if str(candidate.get("app_type") or requested).casefold() == "excel":
                    return self._runtime_excel_document(candidate)
                name = str(candidate.get("document_name") or "현재 문서")
                label = APP_LABELS.get(
                    str(candidate.get("app_type") or requested).casefold(),
                    "Office",
                )
                raise EditDocumentOpenTimeout(
                    f"열려 있는 {label} 문서 '{name}'은 아직 저장되지 않았습니다. "
                    "먼저 Ctrl+S로 저장한 뒤 다시 연결해주세요."
                )
            label = APP_LABELS.get(requested, "Office 또는 한글")
            raise EditDocumentOpenTimeout(
                f"저장된 {label} 문서를 찾지 못했습니다. 문서를 연 뒤 다시 시도해주세요."
            )
        if len(supported) > 1:
            ranked_supported = [
                item for item in supported
                if isinstance(item.get("window_rank"), int)
            ]
            if ranked_supported:
                return min(
                    ranked_supported,
                    key=lambda item: item["window_rank"],
                )
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
