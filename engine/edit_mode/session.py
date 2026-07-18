"""Process-local edit sessions containing only rediscoverable document identity."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Mapping

from engine.edit_mode.contracts import EditRequest
from engine.edit_mode.state_machine import EditSessionState, EditSessionStateMachine


class EditSessionError(RuntimeError):
    error_type = "validation_error"
    status = "blocked"


class EditSessionNotFound(EditSessionError):
    error_type = "target_not_found"


class EditSessionStale(EditSessionError):
    status = "stale_context"


class EditSessionBusy(EditSessionError):
    error_type = "busy"
    status = "busy"


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_document_path(value) -> str:
    """Return one absolute, case-normalized existing file path."""
    raw = str(value or "").strip().strip('"')
    if not raw or "\x00" in raw:
        raise EditSessionError("문서 경로가 비어 있거나 올바르지 않습니다.")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise EditSessionNotFound("선택한 문서 파일을 찾지 못했습니다.") from error
    if not path.is_file():
        raise EditSessionNotFound("선택한 경로는 문서 파일이 아닙니다.")
    return os.path.normcase(str(path))


def document_identity_fingerprint(file_path: str, app_type: str) -> str:
    """Fingerprint the file identity without hashing or uploading its contents."""
    path = canonical_document_path(file_path)
    try:
        stat = os.stat(path)
    except OSError as error:
        raise EditSessionNotFound("연결된 문서 파일을 다시 확인하지 못했습니다.") from error
    identity = "\x00".join((
        str(app_type or "").strip().casefold(),
        path,
        str(getattr(stat, "st_dev", 0)),
        str(getattr(stat, "st_ino", 0)),
    ))
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest().upper()


def runtime_document_identity_fingerprint(
    app_type: str,
    runtime_document_id: str,
    window_handle: int,
) -> str:
    """Fingerprint one process-local unsaved document identity."""
    normalized_app = str(app_type or "").strip().casefold()
    runtime_id = str(runtime_document_id or "").strip().upper()
    handle = int(window_handle or 0)
    if normalized_app != "excel" or not runtime_id or handle <= 0:
        raise EditSessionError("미저장 Excel 문서의 임시 식별 정보가 불완전합니다.")
    identity = "\x00".join(("runtime", normalized_app, runtime_id, str(handle)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest().upper()


@dataclass(frozen=True)
class EditSession:
    session_id: str
    app_type: str
    file_path: str
    document_name: str
    document_fingerprint: str
    identity_kind: str = "file"
    runtime_document_id: str | None = None
    is_saved: bool = True
    window_handle: int = 0
    active_container: str | None = None
    selection_reference: str | None = None
    launch_requested: bool = False
    connected_at: str = ""
    last_target: dict | None = None
    last_action: dict | None = None
    undo_record: dict | None = None

    def to_dict(self) -> dict:
        return copy.deepcopy(asdict(self))


class EditSessionManager:
    """Own exactly one active edit session and no live Office/HWP references."""

    def __init__(self):
        self._lock = threading.RLock()
        self._active_session: EditSession | None = None
        self._state_machine: EditSessionStateMachine | None = None

    def _snapshot_locked(self) -> dict | None:
        if self._active_session is None or self._state_machine is None:
            return None
        result = self._active_session.to_dict()
        last_action = result.get("last_action")
        undo_record = result.get("undo_record")
        if isinstance(last_action, dict):
            result["last_action"] = {
                key: copy.deepcopy(last_action.get(key))
                for key in (
                    "operation",
                    "original_command",
                    "before_preview",
                    "after_preview",
                    "sequence_count",
                    "completed_at",
                )
                if last_action.get(key) is not None
            }
        if isinstance(undo_record, dict):
            result["undo_record"] = {
                "available": True,
                "operation": undo_record.get("operation"),
                "target": undo_record.get("target"),
            }
        state = self._state_machine.to_dict()
        result.update({
            "state": state["state"],
            "revision": state["revision"],
        })
        return result

    def current(self) -> dict | None:
        with self._lock:
            return self._snapshot_locked()

    @staticmethod
    def _json_state(value, label):
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise EditSessionError(f"{label}은 JSON 값이어야 합니다.") from error
        if len(encoded) > 512 * 1024:
            raise EditSessionError(f"{label} 크기가 안전 한도를 넘었습니다.")
        return json.loads(encoded.decode("utf-8"))

    def continuation_state(self, session_id: str | None = None) -> dict:
        """Return the private process-local continuation and undo records."""
        with self._lock:
            if self._active_session is None:
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            if session_id and session_id != self._active_session.session_id:
                raise EditSessionStale("다른 편집 세션의 후속 문맥 요청을 거부했습니다.")
            return {
                "last_target": copy.deepcopy(self._active_session.last_target),
                "last_action": copy.deepcopy(self._active_session.last_action),
                "undo_record": copy.deepcopy(self._active_session.undo_record),
            }

    def record_committed_edit(
        self,
        session_id: str,
        *,
        last_target,
        last_action,
        undo_record,
    ) -> dict:
        """Remember one verified edit without retaining native COM objects."""
        with self._lock:
            if self._active_session is None or session_id != self._active_session.session_id:
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            target = self._json_state(last_target or {}, "직전 편집 대상")
            action = self._json_state(last_action or {}, "직전 편집 작업")
            undo = (
                self._json_state(undo_record, "직전 되돌리기 기록")
                if undo_record
                else None
            )
            self._active_session = replace(
                self._active_session,
                last_target=target,
                last_action=action,
                undo_record=undo,
            )
            return self._snapshot_locked()

    def clear_continuation(
        self,
        session_id: str,
        *,
        clear_undo: bool = True,
    ) -> dict:
        """Discard stale follow-up context, optionally including last undo."""
        with self._lock:
            if self._active_session is None or session_id != self._active_session.session_id:
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            self._active_session = replace(
                self._active_session,
                last_target=None,
                last_action=None,
                undo_record=None if clear_undo else self._active_session.undo_record,
            )
            return self._snapshot_locked()

    def assert_connectable(self) -> None:
        """Reject a replacement before another native document is opened."""
        with self._lock:
            if self._state_machine is None:
                return
            if self._state_machine.state not in {
                EditSessionState.READY,
                EditSessionState.COMMITTED,
                EditSessionState.ROLLED_BACK,
                EditSessionState.STALE_CONTEXT,
                EditSessionState.FAILED,
            }:
                raise EditSessionBusy(
                    "현재 편집 작업이 끝나기 전에는 다른 문서를 연결할 수 없습니다."
                )

    def connect(self, document: Mapping) -> dict:
        """Create a new READY session from verified, serializable metadata."""
        if not isinstance(document, Mapping):
            raise EditSessionError("연결할 문서 정보가 올바르지 않습니다.")
        app_type = str(document.get("app_type") or "").strip().casefold()
        window_handle = max(0, int(document.get("window_handle") or 0))
        identity_kind = str(document.get("identity_kind") or "").casefold()
        runtime_identity = (
            identity_kind == "runtime"
            or document.get("is_saved") is False
            or not str(document.get("file_path") or "").strip()
        )
        if runtime_identity:
            if app_type != "excel":
                raise EditSessionError("미저장 문서 연결은 현재 Excel만 지원합니다.")
            file_path = ""
            document_name = str(
                document.get("document_name") or "현재 통합문서"
            )
            runtime_document_id = str(
                document.get("runtime_document_id") or ""
            ).strip().upper()
            fingerprint = runtime_document_identity_fingerprint(
                app_type,
                runtime_document_id,
                window_handle,
            )
            identity_kind = "runtime"
            is_saved = False
        else:
            file_path = canonical_document_path(document.get("file_path"))
            fingerprint = document_identity_fingerprint(file_path, app_type)
            document_name = str(
                document.get("document_name") or Path(file_path).name
            )
            runtime_document_id = None
            identity_kind = "file"
            is_saved = True
        active_container = str(document.get("active_container") or "").strip() or None
        selection_reference = (
            str(document.get("selection_reference") or "").strip() or None
        )

        with self._lock:
            self.assert_connectable()
            if self._active_session is not None:
                self._disconnect_locked(reason="다른 문서로 연결 전환")
            machine = EditSessionStateMachine()
            machine.transition(EditSessionState.ATTACHING, reason="문서 연결 확인 완료")
            session = EditSession(
                session_id=f"edit-{uuid.uuid4().hex}",
                app_type=app_type,
                file_path=file_path,
                document_name=document_name[:260],
                document_fingerprint=fingerprint,
                identity_kind=identity_kind,
                runtime_document_id=runtime_document_id,
                is_saved=is_saved,
                window_handle=window_handle,
                active_container=active_container,
                selection_reference=selection_reference,
                launch_requested=bool(document.get("launch_requested")),
                connected_at=_timestamp(),
            )
            machine.transition(EditSessionState.READY, reason="편집 대상 문서 고정")
            self._active_session = session
            self._state_machine = machine
            return self._snapshot_locked()

    def promote_runtime_document(
        self,
        session_id: str,
        file_path: str,
        *,
        document_name: str | None = None,
        runtime_document_id: str | None = None,
    ) -> dict:
        """Convert the same live Excel workbook to durable file identity."""
        canonical_path = canonical_document_path(file_path)
        with self._lock:
            if self._active_session is None or self._state_machine is None:
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            session = self._active_session
            if session.session_id != str(session_id):
                raise EditSessionStale("다른 편집 세션의 문서 신원 변경을 거부했습니다.")
            if session.identity_kind != "runtime":
                return self._snapshot_locked()
            if self._state_machine.state is not EditSessionState.READY:
                raise EditSessionBusy(
                    "편집 작업이 진행 중이라 저장된 문서 신원 전환을 잠시 미뤘습니다."
                )
            actual_runtime_id = str(runtime_document_id or "").strip().upper()
            if (
                actual_runtime_id
                and actual_runtime_id != str(session.runtime_document_id or "").upper()
            ):
                raise EditSessionStale(
                    "저장된 통합문서가 처음 연결한 임시 문서와 달라 전환하지 않았습니다."
                )
            self._active_session = replace(
                session,
                file_path=canonical_path,
                document_name=str(
                    document_name or Path(canonical_path).name
                )[:260],
                document_fingerprint=document_identity_fingerprint(
                    canonical_path,
                    session.app_type,
                ),
                identity_kind="file",
                runtime_document_id=None,
                is_saved=True,
                last_target=None,
                last_action=None,
                undo_record=None,
            )
            return self._snapshot_locked()

    def _disconnect_locked(self, *, reason: str) -> dict:
        if self._active_session is None or self._state_machine is None:
            raise EditSessionNotFound("연결된 편집 문서가 없습니다.")
        if self._state_machine.state in {
            EditSessionState.COMMITTED,
            EditSessionState.ROLLED_BACK,
        }:
            self._state_machine.transition(
                EditSessionState.READY,
                reason="완료된 편집 상태 정리",
            )
        if self._state_machine.state not in {
            EditSessionState.READY,
            EditSessionState.STALE_CONTEXT,
            EditSessionState.FAILED,
        }:
            raise EditSessionBusy(
                "편집 작업이 진행 중이라 문서 연결을 해제할 수 없습니다. 작업을 먼저 끝내주세요."
            )
        self._state_machine.transition(EditSessionState.DISCONNECTED, reason=reason)
        result = self._snapshot_locked()
        self._active_session = None
        self._state_machine = None
        return result

    def disconnect(self, session_id: str | None = None) -> dict:
        with self._lock:
            if self._active_session is None:
                raise EditSessionNotFound("연결된 편집 문서가 없습니다.")
            if session_id and str(session_id) != self._active_session.session_id:
                raise EditSessionStale("다른 편집 세션의 연결 해제 요청을 거부했습니다.")
            return self._disconnect_locked(reason="사용자 연결 해제")

    def validate_request(self, request: EditRequest) -> EditSession:
        with self._lock:
            if self._active_session is None or self._state_machine is None:
                raise EditSessionNotFound("편집할 문서를 먼저 연결해주세요.")
            session = self._active_session
            if request.edit_session_id != session.session_id:
                raise EditSessionStale("현재 연결된 문서와 다른 편집 세션 요청입니다.")
            if request.document_fingerprint != session.document_fingerprint:
                raise EditSessionStale("현재 연결된 문서 fingerprint와 요청이 일치하지 않습니다.")
            self._state_machine.require_state(EditSessionState.READY)
            if session.identity_kind == "runtime":
                current = runtime_document_identity_fingerprint(
                    session.app_type,
                    session.runtime_document_id or "",
                    session.window_handle,
                )
            else:
                current = document_identity_fingerprint(
                    session.file_path,
                    session.app_type,
                )
            if current != session.document_fingerprint:
                self._state_machine.transition(
                    EditSessionState.STALE_CONTEXT,
                    reason="연결 후 파일 식별 정보 변경",
                )
                raise EditSessionStale(
                    "연결 후 문서 파일이 교체되어 다시 연결해야 합니다."
                )
            return session

    def state_machine_for(self, session_id: str) -> EditSessionStateMachine:
        with self._lock:
            if (
                self._active_session is None
                or self._state_machine is None
                or session_id != self._active_session.session_id
            ):
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            return self._state_machine

    def reset_ready(self, session_id: str, *, reason: str = "편집 작업 정리") -> dict:
        """Return a completed, cancelled, or safely failed session to READY."""
        with self._lock:
            if (
                self._active_session is None
                or self._state_machine is None
                or session_id != self._active_session.session_id
            ):
                raise EditSessionNotFound("현재 편집 세션을 찾지 못했습니다.")
            if self._state_machine.state is EditSessionState.READY:
                return self._snapshot_locked()
            if self._state_machine.state not in {
                EditSessionState.PREPARED,
                EditSessionState.APPROVAL_REQUIRED,
                EditSessionState.COMMITTED,
                EditSessionState.STALE_CONTEXT,
                EditSessionState.FAILED,
                EditSessionState.ROLLED_BACK,
            }:
                raise EditSessionBusy(
                    "실행 중인 편집 작업은 준비 상태로 강제 전환할 수 없습니다."
                )
            self._state_machine.transition(EditSessionState.READY, reason=reason)
            return self._snapshot_locked()
