"""High-level document connection boundary for edit mode."""

from __future__ import annotations

from pathlib import Path

from engine.edit_mode.file_picker import choose_edit_document
from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.session import EditSessionManager
from engine.edit_mode.window_layout import WindowLayoutManager
from engine.execution_result import failure_result
from engine.runtime_paths import USER_DATA_DIR
from engine.storage.json_store import atomic_write_json, safe_read_json


class EditModeController:
    """Connect local documents now and host app edit adapters in later stages."""

    def __init__(
        self,
        *,
        intake_manager=None,
        session_manager=None,
        layout_manager=None,
        file_picker=None,
        settings_path=None,
    ):
        self.intake_manager = intake_manager or FileIntakeManager()
        self.session_manager = session_manager or EditSessionManager()
        self._persist_layout = layout_manager is None or settings_path is not None
        self._settings_path = Path(
            settings_path or (Path(USER_DATA_DIR) / "edit_mode_settings.json")
        )
        saved_layout = self._load_auto_layout()
        self.layout_manager = layout_manager or WindowLayoutManager(
            enabled=saved_layout
        )
        if layout_manager is not None and settings_path is not None:
            self.layout_manager.set_enabled(saved_layout)
        self.file_picker = file_picker or choose_edit_document

    def _load_auto_layout(self) -> bool:
        if not self._persist_layout:
            return True
        data = safe_read_json(
            self._settings_path,
            {"schema_version": 1, "auto_layout": True},
        )
        return bool(data.get("auto_layout", True)) if isinstance(data, dict) else True

    def _connect_metadata(self, document: dict) -> dict:
        session = self.session_manager.connect(document)
        layout = self.layout_manager.arrange(
            session["session_id"], session.get("window_handle", 0)
        )
        result = dict(session)
        result["layout"] = layout
        return result

    def connect_file(self, file_path: str) -> dict:
        self.session_manager.assert_connectable()
        return self._connect_metadata(self.intake_manager.connect_file(file_path))

    def choose_and_connect(self) -> dict | None:
        self.session_manager.assert_connectable()
        selected = self.file_picker()
        return self.connect_file(selected) if selected else None

    def connect_dropped_document(
        self,
        *,
        file_name: str,
        file_size=None,
        path_hint: str | None = None,
    ) -> dict:
        self.session_manager.assert_connectable()
        document = self.intake_manager.connect_dropped_document(
            file_name=file_name,
            file_size=file_size,
            path_hint=path_hint,
        )
        return self._connect_metadata(document)

    def connect_active_document(self, app_type: str | None = None) -> dict:
        self.session_manager.assert_connectable()
        document = self.intake_manager.connect_active_document(app_type)
        return self._connect_metadata(document)

    def disconnect(self, session_id: str | None = None) -> dict:
        return self.session_manager.disconnect(session_id)

    def status(self) -> dict:
        session = self.session_manager.current()
        return {
            "connected": session is not None,
            "session": session,
            "auto_layout": self.layout_manager.enabled,
        }

    def set_auto_layout(self, enabled) -> dict:
        value = self.layout_manager.set_enabled(enabled)
        if self._persist_layout:
            atomic_write_json(
                self._settings_path,
                {"schema_version": 1, "auto_layout": value},
            )
        return {
            "success": True,
            "auto_layout": value,
        }

    def handle(self, request):
        session = self.session_manager.validate_request(request)
        return failure_result(
            "문서 연결과 대상 고정은 완료됐습니다. 실제 문서 내용 편집은 다음 문맥·앱 어댑터 단계에서 활성화됩니다.",
            action="edit",
            target=session.session_id,
            error_type="validation_error",
            status="blocked",
            data={
                "edit_session_id": session.session_id,
                "document_name": session.document_name,
                "app_type": session.app_type,
                "document_fingerprint": session.document_fingerprint,
            },
        )
