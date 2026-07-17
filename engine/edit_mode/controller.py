"""High-level document connection boundary for edit mode."""

from __future__ import annotations

from pathlib import Path
import uuid

from engine.edit_mode.context import EditContextManager
from engine.edit_mode.contracts import EditPreparedAction, EditRequest
from engine.edit_mode.coordinator import EditExecutionCoordinator
from engine.edit_mode.file_picker import choose_edit_document
from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.session import (
    EditSessionBusy,
    EditSessionManager,
    EditSessionNotFound,
    EditSessionStale,
)
from engine.edit_mode.stage5 import (
    Stage5EditError,
    edit_preview_message,
    edit_success_message,
)
from engine.edit_mode.stage7 import (
    Stage7NativeEditAdapter,
    build_commit_records,
    rewrite_pending_command,
)
from engine.edit_mode.stage9 import Stage9NativeEditAdapter
from engine.edit_mode.stage11 import Stage11NativeEditAdapter
from engine.edit_mode.state_machine import EditSessionState
from engine.edit_mode.window_layout import WindowLayoutManager
from engine.execution_result import success_result
from engine.runtime_paths import USER_DATA_DIR
from engine.storage.json_store import atomic_write_json, safe_read_json


class EditModeController:
    """Connect local documents and expose their current read-only native context."""

    def __init__(
        self,
        *,
        intake_manager=None,
        session_manager=None,
        layout_manager=None,
        context_manager=None,
        native_action_registry=None,
        file_picker=None,
        settings_path=None,
        workflow_executor=None,
        user_learning_manager=None,
    ):
        self.intake_manager = intake_manager or FileIntakeManager()
        self.session_manager = session_manager or EditSessionManager()
        self.context_manager = context_manager or EditContextManager()
        self._native_action_registry = native_action_registry
        self._workflow_executor = workflow_executor
        self._user_learning_manager = user_learning_manager
        self._parser = None
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

    def bind_parser(self, parser) -> None:
        """Bind confirmation/runtime services without creating an import cycle."""
        self._parser = parser

    def _learning_manager(self):
        if self._user_learning_manager is None:
            from engine.learning import UserPreferenceLearningManager

            self._user_learning_manager = UserPreferenceLearningManager()
        return self._user_learning_manager

    def user_preference_learning_status(self) -> dict:
        manager = self._learning_manager()
        return {
            "active_preferences": manager.active_preferences(),
            "candidates": manager.list_candidates(include_observing=True),
        }

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
        context = None
        context_error = None
        try:
            context = self.context_manager.capture(session)
        except Exception as error:
            context_error = self._context_error(error)
        layout = self.layout_manager.arrange(
            session["session_id"], session.get("window_handle", 0)
        )
        result = dict(session)
        result["layout"] = layout
        result["context"] = context
        result["context_error"] = context_error
        return result

    @staticmethod
    def _context_error(error) -> dict:
        return {
            "message": str(error),
            "error_type": getattr(error, "error_type", "execution_error"),
            "status": getattr(error, "status", "failed"),
        }

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

    def _clear_continuation(self, session_id: str) -> None:
        try:
            self.session_manager.clear_continuation(session_id)
        except EditSessionNotFound:
            pass

    def _guard_continuation(self, session: dict, context: dict) -> dict:
        state = self.session_manager.continuation_state(session["session_id"])
        last_action = dict(state.get("last_action") or {})
        if not last_action:
            return session
        expected = str(
            last_action.get("post_context_fingerprint") or ""
        ).upper()
        actual = str(context.get("context_fingerprint") or "").upper()
        if not expected or expected != actual:
            return self.session_manager.clear_continuation(session["session_id"])
        return session

    def status(self) -> dict:
        session = self.session_manager.current()
        context = None
        context_error = None
        if session is not None:
            try:
                context = self.context_manager.capture(session)
                session = self._guard_continuation(session, context)
            except Exception as error:
                context_error = self._context_error(error)
                self._clear_continuation(session["session_id"])
                session = self.session_manager.current()
        return {
            "connected": session is not None,
            "session": session,
            "context": context,
            "context_error": context_error,
            "auto_layout": self.layout_manager.enabled,
        }

    def context(self, session_id: str | None = None) -> dict:
        session = self.session_manager.current()
        if session is None:
            raise EditSessionNotFound("현재 연결된 편집 문서가 없습니다.")
        if session_id and str(session_id) != session["session_id"]:
            raise EditSessionStale("다른 편집 세션의 문맥 조회 요청을 거부했습니다.")
        try:
            context = self.context_manager.capture(session)
            self._guard_continuation(session, context)
            return context
        except Exception:
            self._clear_continuation(session["session_id"])
            raise

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

    def _registry(self):
        registry = self._native_action_registry
        if registry is None and self._parser is not None:
            registry = getattr(self._parser, "app_action_registry", None)
        if registry is None:
            raise Stage5EditError("문서 편집용 네이티브 어댑터가 준비되지 않았습니다.")
        return registry

    def _edit_adapter(self, session):
        app_type = str(session.get("app_type") or "").casefold()
        if app_type not in {"excel", "hwp", "word", "powerpoint"}:
            raise Stage5EditError(
                "현재 실제 편집은 Excel·한글·Word·PowerPoint를 지원합니다."
            )
        native = self._registry().get(app_type)
        adapter_class = Stage11NativeEditAdapter
        kwargs = {"user_learning_manager": self._learning_manager()}
        if self._workflow_executor is None:
            from engine.workflows import WorkflowExecutor

            self._workflow_executor = WorkflowExecutor()
        kwargs["workflow_executor"] = self._workflow_executor
        return adapter_class(
            session,
            self.context_manager,
            native,
            continuation_state=self.session_manager.continuation_state(
                session["session_id"]
            ),
            **kwargs,
        )

    def _reset_if_safe(self, session_id: str, *, reason: str) -> None:
        try:
            machine = self.session_manager.state_machine_for(session_id)
            if machine.state in {
                EditSessionState.PREPARED,
                EditSessionState.APPROVAL_REQUIRED,
                EditSessionState.COMMITTED,
                EditSessionState.STALE_CONTEXT,
                EditSessionState.FAILED,
                EditSessionState.ROLLED_BACK,
            }:
                self.session_manager.reset_ready(session_id, reason=reason)
        except (EditSessionNotFound, EditSessionBusy):
            pass

    @staticmethod
    def _payload_matches_request(payload, request: EditRequest) -> bool:
        saved = payload.get("edit_request", {}) if isinstance(payload, dict) else {}
        return (
            saved.get("edit_session_id") == request.edit_session_id
            and saved.get("document_fingerprint") == request.document_fingerprint
            and saved.get("context_fingerprint") == request.context_fingerprint
            and saved.get("request_id") == request.request_id
        )

    def _recover_pending_confirmation(self, request, chat_session_id):
        if self._parser is None:
            return None
        machine = self.session_manager.state_machine_for(request.edit_session_id)
        if machine.state is not EditSessionState.APPROVAL_REQUIRED:
            return None
        record = self._parser.pending_confirmation_manager.active_record(chat_session_id)
        if (
            record
            and record.get("payload", {}).get("kind") == "prepared_edit_action"
            and self._payload_matches_request(record.get("payload", {}), request)
        ):
            return self._parser._confirmation_result(record)
        if record is None:
            self.session_manager.reset_ready(
                request.edit_session_id,
                reason="만료된 편집 승인 요청 정리",
            )
            return None
        raise EditSessionBusy("이 대화에는 이미 다른 확인 요청이 대기 중입니다.")

    def _queue_confirmation(self, request, prepared, chat_session_id):
        if self._parser is None:
            raise Stage5EditError("편집 미리보기를 표시할 확인 서비스가 준비되지 않았습니다.")
        preview = dict(prepared.metadata.get("preview") or {})
        target = (
            preview.get("target")
            or prepared.target.get("selection_reference")
            or "현재 선택"
        )
        options = [
            {
                "id": "apply",
                "label": "적용",
                "description": "표시된 대상에만 적용하고 다시 읽어 결과를 검증합니다.",
                "danger": True,
                "aliases": ["예", "네", "응", "진행", "적용해", "실행"],
            }
        ]
        if prepared.metadata.get("rewrite_supported"):
            options.append({
                "id": "rewrite",
                "label": "다시 작성",
                "description": "현재 미리보기를 버리고 다른 수정안을 다시 만듭니다.",
                "aliases": ["다시", "그거 말고", "재작성", "다시 작성"],
            })
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "문서를 변경하지 않고 취소합니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마", "취소해"],
        })
        record = self._parser.pending_confirmation_manager.create(
            session_id=chat_session_id,
            execution_id=self._parser._current_execution_id(),
            original_command=request.text,
            reason="document_edit_preview",
            message=edit_preview_message(prepared),
            action="edit",
            target=target,
            options=options,
            payload={
                "kind": "prepared_edit_action",
                "prepared_action": prepared.to_dict(),
                "edit_request": request.to_dict(),
            },
        )
        return self._parser._confirmation_result(record)

    def _success_result(self, session, request, prepared, result):
        post_context = None
        context_error = None
        updated_session = self.session_manager.current() or session
        try:
            post_context = self.context_manager.capture(session)
        except Exception as error:
            context_error = self._context_error(error)
        if result.changed:
            if prepared.operation == "undo_last_edit":
                self._clear_continuation(session["session_id"])
                updated_session = self.session_manager.current() or session
            elif post_context is not None:
                try:
                    previous = self.session_manager.continuation_state(
                        session["session_id"]
                    )
                    last_action, undo_record = build_commit_records(
                        request,
                        prepared,
                        result,
                        post_context,
                        previous,
                    )
                    updated_session = self.session_manager.record_committed_edit(
                        session["session_id"],
                        last_target=post_context.get("target") or {},
                        last_action=last_action,
                        undo_record=undo_record,
                    )
                except Exception as error:
                    self._clear_continuation(session["session_id"])
                    context_error = self._context_error(error)
                    updated_session = self.session_manager.current() or session
            else:
                self._clear_continuation(session["session_id"])
                updated_session = self.session_manager.current() or session
        last_action = dict(updated_session.get("last_action") or {})
        undo_record = dict(updated_session.get("undo_record") or {})
        message = (
            "직전 JARVIS 편집을 원래 상태로 복원했고 결과를 다시 확인했습니다."
            if prepared.operation == "undo_last_edit"
            else edit_success_message(prepared, result)
        )
        data = {
            "edit_session_id": session["session_id"],
            "document_name": session["document_name"],
            "app_type": session["app_type"],
            "operation": prepared.operation,
            "changed": result.changed,
            "action_id": prepared.action_id,
            "preview": dict(prepared.metadata.get("preview") or {}),
            "context": post_context,
            "context_error": context_error,
            "sequence_count": int(last_action.get("sequence_count") or 0),
            "undo_available": bool(undo_record.get("available")),
        }
        if (
            prepared.metadata.get("vba")
            or prepared.metadata.get("workflow")
            or prepared.metadata.get("user_preference")
        ):
            data["observations"] = dict(result.observations)
        return success_result(
            message,
            action="edit",
            target=session["session_id"],
            verified=result.verified,
            data=data,
        )

    def _queue_vba_reconfirmation(
        self,
        request,
        prepared,
        chat_session_id,
    ):
        if self._parser is None:
            raise Stage5EditError("고위험 VBA 재확인 서비스를 사용할 수 없습니다.")
        capabilities = list(
            prepared.metadata.get("dangerous_capabilities") or []
        )
        labels = {
            "shell": "외부 프로그램 실행",
            "file_delete": "파일·폴더 삭제",
            "network": "네트워크 통신",
            "registry": "레지스트리 접근",
        }
        warning = ", ".join(labels.get(item, item) for item in capabilities)
        record = self._parser.pending_confirmation_manager.create(
            session_id=chat_session_id,
            execution_id=self._parser._current_execution_id(),
            original_command=request.text,
            reason="high_risk_vba_reconfirmation",
            message=(
                "고위험 VBA 작업을 다시 확인합니다.\n"
                f"감지된 기능: {warning or '고위험 외부 동작'}\n"
                "이 코드는 파일, 프로그램, 네트워크 또는 시스템 설정에 영향을 줄 수 있습니다. "
                "그래도 현재 미리보기의 작업을 실행할까요?"
            ),
            action="edit",
            target=prepared.target.get("native_target") or "VBA",
            options=[
                {
                    "id": "apply",
                    "label": "위험을 이해하고 실행",
                    "description": "두 번째 승인 후에만 VBA 작업을 실행합니다.",
                    "danger": True,
                    "aliases": ["실행", "진행", "동의", "확인"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "VBA 코드를 수정하거나 실행하지 않습니다.",
                    "cancel": True,
                    "aliases": ["취소", "그만", "하지마", "아니요"],
                },
            ],
            payload={
                "kind": "prepared_edit_action",
                "prepared_action": prepared.to_dict(),
                "edit_request": request.to_dict(),
                "approval_round": 2,
            },
        )
        return self._parser._confirmation_result(record)

    def handle(self, request, *, chat_session_id=None, log_callback=None):
        pending = self._recover_pending_confirmation(request, chat_session_id)
        if pending is not None:
            return pending
        session_value = self.session_manager.validate_request(request)
        session = session_value.to_dict()
        try:
            initial_context = self.context_manager.capture(session)
            session = self._guard_continuation(session, initial_context)
        except Exception:
            self._clear_continuation(session["session_id"])
            raise
        coordinator = EditExecutionCoordinator(
            self._edit_adapter(session),
            self.session_manager.state_machine_for(session["session_id"]),
        )
        try:
            prepared = coordinator.prepare(request)
            if log_callback:
                log_callback(
                    f"[Edit] prepared operation={prepared.operation} action={prepared.action_id}"
                )
            if prepared.requires_approval:
                return self._queue_confirmation(request, prepared, chat_session_id)
            result = coordinator.execute(prepared)
            response = self._success_result(session, request, prepared, result)
            self.session_manager.reset_ready(
                session["session_id"], reason="검증된 편집 작업 완료"
            )
            return response
        except Exception:
            self._reset_if_safe(
                session["session_id"], reason="실행되지 않은 편집 작업 정리"
            )
            raise

    def resolve_prepared_edit(
        self,
        payload,
        *,
        chat_session_id=None,
        confirmation_id=None,
        log_callback=None,
    ):
        request = EditRequest(**dict(payload.get("edit_request") or {}))
        prepared = EditPreparedAction.from_dict(
            dict(payload.get("prepared_action") or {})
        )
        current = self.session_manager.current()
        if current is None:
            raise EditSessionNotFound("승인한 편집 문서의 연결이 해제되었습니다.")
        if (
            current["session_id"] != request.edit_session_id
            or current["document_fingerprint"] != request.document_fingerprint
            or prepared.edit_session_id != request.edit_session_id
            or prepared.request_id != request.request_id
        ):
            raise EditSessionStale("승인한 작업과 현재 편집 문서가 달라 실행하지 않았습니다.")
        try:
            approval_context = self.context_manager.capture(current)
            current = self._guard_continuation(current, approval_context)
        except Exception:
            self._clear_continuation(current["session_id"])
            raise
        machine = self.session_manager.state_machine_for(current["session_id"])
        state = machine.to_dict()
        if (
            machine.state is not EditSessionState.APPROVAL_REQUIRED
            or state.get("active_action_id") != prepared.action_id
        ):
            raise EditSessionStale("승인 대기 중인 편집 작업이 아니어서 실행하지 않았습니다.")

        if (
            prepared.metadata.get("requires_reconfirmation")
            and int(payload.get("approval_round") or 1) < 2
        ):
            return self._queue_vba_reconfirmation(
                request,
                prepared,
                chat_session_id,
            )

        coordinator = EditExecutionCoordinator(
            self._edit_adapter(current),
            machine,
        )
        try:
            if log_callback:
                log_callback(
                    "[Edit] approved "
                    f"confirmation={confirmation_id or ''} action={prepared.action_id}"
                )
            result = coordinator.execute(prepared, approved=True)
            response = self._success_result(current, request, prepared, result)
            self.session_manager.reset_ready(
                current["session_id"], reason="승인된 편집 작업 완료"
            )
            return response
        except Exception:
            self._reset_if_safe(
                current["session_id"], reason="실패하거나 오래된 편집 작업 정리"
            )
            raise

    def rewrite_pending_edit(
        self,
        payload,
        *,
        chat_session_id=None,
        confirmation_id=None,
        log_callback=None,
    ):
        """Discard one pending preview and prepare a bounded local alternative."""
        request = EditRequest(**dict(payload.get("edit_request") or {}))
        prepared = EditPreparedAction.from_dict(
            dict(payload.get("prepared_action") or {})
        )
        current = self.session_manager.current()
        if current is None or current["session_id"] != request.edit_session_id:
            raise EditSessionStale(
                "다시 작성할 편집 문서가 현재 연결된 문서와 다릅니다."
            )
        machine = self.session_manager.state_machine_for(current["session_id"])
        state = machine.to_dict()
        if (
            machine.state is not EditSessionState.APPROVAL_REQUIRED
            or state.get("active_action_id") != prepared.action_id
        ):
            raise EditSessionStale("다시 작성할 편집 미리보기가 더 이상 유효하지 않습니다.")
        rewritten = rewrite_pending_command(prepared)
        self.session_manager.reset_ready(
            current["session_id"],
            reason="기존 편집 미리보기 재작성",
        )
        if log_callback:
            log_callback(
                "[Edit] rewrite "
                f"confirmation={confirmation_id or ''} action={prepared.action_id}"
            )
        next_request = EditRequest(
            text=rewritten,
            edit_session_id=request.edit_session_id,
            document_fingerprint=request.document_fingerprint,
            request_id=uuid.uuid4().hex,
            context_fingerprint=prepared.context_fingerprint,
        )
        return self.handle(
            next_request,
            chat_session_id=chat_session_id,
            log_callback=log_callback,
        )

    def cancel_pending_edit(self, payload) -> None:
        prepared = EditPreparedAction.from_dict(
            dict(payload.get("prepared_action") or {})
        )
        if (
            prepared.operation == "activate_user_preference"
            and self._user_learning_manager is not None
        ):
            self._user_learning_manager.dismiss(
                prepared.arguments.get("candidate_id")
            )
        try:
            machine = self.session_manager.state_machine_for(
                prepared.edit_session_id
            )
        except EditSessionNotFound:
            return
        state = machine.to_dict()
        if (
            machine.state is EditSessionState.APPROVAL_REQUIRED
            and state.get("active_action_id") == prepared.action_id
        ):
            self.session_manager.reset_ready(
                prepared.edit_session_id,
                reason="사용자가 편집 미리보기를 취소함",
            )
