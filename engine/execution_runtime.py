"""Execution cancellation, structured results, and bounded diagnostics."""

import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from engine.diagnostics import (
    DiagnosticIncidentManager,
    privacy_safe_execution_record,
)
from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json
from engine.version import runtime_info


DIAGNOSTICS_PATH = user_data_path("execution_diagnostics.json")
_DEFAULT_INCIDENT_MANAGER = object()


class ExecutionCancelled(RuntimeError):
    pass


class ExecutionBusyError(RuntimeError):
    """Reject a new execution while another one is already running.

    A single ``CommandParser`` instance can be entered from several eel
    callback threads (voice, auto-run, a second window). Without this guard
    ``begin`` would overwrite the running execution's diagnostics and clear its
    pending cancel signal. Callers surface it as a retryable ``busy`` result.
    """

    error_type = "busy"
    status = "busy"
    retryable = True


class ExecutionController:
    def __init__(
        self,
        diagnostics_path=None,
        max_records=100,
        incident_manager=_DEFAULT_INCIDENT_MANAGER,
        event_observer=None,
    ):
        self.diagnostics_path = diagnostics_path or DIAGNOSTICS_PATH
        self.max_records = max_records
        self.incident_manager = (
            DiagnosticIncidentManager(
                Path(self.diagnostics_path).with_name("diagnostic_incidents.json")
            )
            if incident_manager is _DEFAULT_INCIDENT_MANAGER
            else incident_manager
        )
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._event_observer = event_observer if callable(event_observer) else None
        self.current = None
        loaded = safe_read_json(self.diagnostics_path, {"records": []})
        records = loaded.get("records", []) if isinstance(loaded, dict) else []
        raw_records = list(records)[-max_records:] if isinstance(records, list) else []
        self.records = [privacy_safe_execution_record(item) for item in raw_records]
        self._migrate_private_diagnostics(raw_records)
        # Paused confirmations are process-local and intentionally never loaded
        # from diagnostics after a restart.
        self.pending = {}

    def set_event_observer(self, observer):
        """Observe existing runtime events without changing their persistence."""
        with self._lock:
            self._event_observer = observer if callable(observer) else None

    def _migrate_private_diagnostics(self, raw_records):
        """Rewrite legacy raw logs and their recovery backup with safe fields."""
        path = Path(self.diagnostics_path)
        if not path.is_file():
            return
        backup = safe_read_json(f"{path}.bak", None, recover=False)
        backup_records = (
            backup.get("records", []) if isinstance(backup, dict) else []
        )
        primary_changed = self.records != raw_records
        backup_changed = bool(backup_records) and [
            privacy_safe_execution_record(item) for item in backup_records
        ] != backup_records
        if not primary_changed and not backup_changed:
            return
        try:
            payload = {"records": self.records}
            # First replaces the legacy primary; second replaces the .bak made
            # from that primary with the already-sanitized representation.
            atomic_write_json(path, payload)
            atomic_write_json(path, payload)
        except OSError:
            pass

    def can_begin_command(self):
        """Whether the single command slot is free for a new command."""
        with self._lock:
            return self.current is None and not self.pending

    def can_resume(self, execution_id):
        """Check a paused execution without consuming its pending record."""
        with self._lock:
            return (
                self.current is None
                and str(execution_id or "") in self.pending
            )

    def begin(self, label="command", metadata=None):
        with self._lock:
            # Reject before clearing the cancel event so a rejected second
            # command never wipes the running command's pending cancellation.
            if self.current is not None:
                raise ExecutionBusyError(
                    "이미 실행 중인 작업이 있어 새 명령을 시작할 수 없습니다. "
                    "현재 작업이 끝난 뒤 다시 시도해 주세요."
                )
            if self.pending:
                raise ExecutionBusyError(
                    "A command is waiting for confirmation. Confirm or cancel it first."
                )
            self._cancel_event.clear()
            execution_id = uuid.uuid4().hex
            identity = runtime_info()
            self.current = {
                "execution_id": execution_id,
                "app_version": identity["app_version"],
                "git_commit": identity["git_commit"],
                "build_kind": identity["build_kind"],
                "label": str(label)[:200],
                "status": "running",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "started_monotonic": time.monotonic(),
                "metadata": dict(metadata or {}),
                "events": [],
            }
            return execution_id

    def event(self, action, status="running", details=None):
        observer = None
        payload = None
        with self._lock:
            if not self.current:
                return
            event = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": str(action),
                "status": str(status),
                "details": details if isinstance(details, dict) else {},
            }
            self.current["events"].append(event)
            observer = self._event_observer
            if observer is not None:
                payload = {
                    "execution_id": self.current.get("execution_id", ""),
                    **event,
                }
        if observer is not None and payload is not None:
            try:
                observer(payload)
            except Exception:
                # User feedback is observational and must never alter execution.
                pass

    def pending_event(self, execution_id, action, status="pending", details=None):
        """Append diagnostics to a paused execution without changing state."""
        with self._lock:
            record = self.pending.get(str(execution_id or ""))
            if not record:
                return False
            record["events"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": str(action),
                "status": str(status),
                "details": details if isinstance(details, dict) else {},
            })
            return True

    def cancel(self):
        with self._lock:
            if not self.current or self.current.get("status") != "running":
                return False
            self._cancel_event.set()
            self.event("execution", "cancel_requested")
            return True

    def check_cancelled(self):
        if self._cancel_event.is_set():
            raise ExecutionCancelled("사용자가 실행을 취소했습니다.")

    def wait(self, seconds):
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            self.check_cancelled()
            self._cancel_event.wait(min(0.05, deadline - time.monotonic()))
        self.check_cancelled()

    def finish(
        self,
        success,
        status=None,
        response="",
        error="",
        extra=None,
        expected_execution_id=None,
    ):
        with self._lock:
            if not self.current:
                return None
            if (
                expected_execution_id is not None
                and self.current.get("execution_id")
                != str(expected_execution_id or "")
            ):
                return None
            record = dict(self.current)
            started = record.pop("started_monotonic", time.monotonic())
            record.update({
                "success": bool(success),
                "status": status or ("success" if success else "failed"),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "response": str(response or "")[:1000],
                "error": str(error or "")[:1000],
            })
            if isinstance(extra, dict):
                record.update(extra)
            if self.incident_manager is not None and not record["success"]:
                try:
                    incident = self.incident_manager.record_execution_failure(record)
                    if incident is not None:
                        record["diagnostic_incident_id"] = incident["incident_id"]
                        record["failure_triage"] = incident.get("triage")
                        if incident.get("developer_issue_id"):
                            record["developer_issue_id"] = incident["developer_issue_id"]
                # Diagnostics must never replace or alter the command result.
                except Exception:
                    pass
            safe_record = privacy_safe_execution_record(record)
            self.records.append(safe_record)
            self.records = self.records[-self.max_records:]
            self.current = None
            self._cancel_event.clear()
            try:
                atomic_write_json(self.diagnostics_path, {"records": self.records})
            except OSError:
                pass
            return safe_record

    def pause_for_confirmation(
        self,
        confirmation_id,
        response="",
        extra=None,
        expected_execution_id=None,
    ):
        """Move the running execution into an in-memory waiting state."""
        return self.pause_for_user_input(
            confirmation_id,
            response=response,
            extra=extra,
            expected_execution_id=expected_execution_id,
            status="confirmation_required",
        )

    def pause_for_user_input(
        self,
        confirmation_id,
        response="",
        extra=None,
        expected_execution_id=None,
        status="confirmation_required",
    ):
        if status not in {"confirmation_required", "clarification_required"}:
            raise ValueError("지원하지 않는 사용자 입력 대기 상태입니다.")
        with self._lock:
            if not self.current:
                return None
            if (
                expected_execution_id is not None
                and self.current.get("execution_id")
                != str(expected_execution_id or "")
            ):
                return None
            record = self.current
            execution_id = record["execution_id"]
            record["status"] = status
            record["paused_at"] = datetime.now().isoformat(timespec="seconds")
            record["confirmation_id"] = str(confirmation_id or "")
            record["response"] = str(response or "")[:1000]
            record["events"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": (
                    "clarification"
                    if status == "clarification_required" else "confirmation"
                ),
                "status": status,
                "details": {"confirmation_id": str(confirmation_id or "")},
            })
            if isinstance(extra, dict):
                record.update(extra)
            self.pending[execution_id] = record
            self.current = None
            self._cancel_event.clear()
            return execution_id

    def resume(self, execution_id):
        """Resume a paused execution after its confirmation was consumed."""
        with self._lock:
            if self.current is not None:
                return False
            execution_id = str(execution_id or "")
            record = self.pending.get(execution_id)
            if not record:
                return False
            # Remove only after the conflict and existence checks pass.  This
            # keeps an unresumable request intact for safe error handling.
            self.pending.pop(execution_id, None)
            record["status"] = "running"
            record["resumed_at"] = datetime.now().isoformat(timespec="seconds")
            record["events"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": "confirmation",
                "status": "resumed",
                "details": {
                    "confirmation_id": record.get("confirmation_id", "")
                },
            })
            self.current = record
            self._cancel_event.clear()
            return True

    def drop_pending(self, execution_id):
        """Forget a stale paused execution without recording it as completed."""
        with self._lock:
            return self.pending.pop(str(execution_id or ""), None) is not None

    def diagnostics(self, limit=20):
        limit = max(1, min(int(limit or 20), self.max_records))
        with self._lock:
            current = (
                privacy_safe_execution_record(self.current)
                if self.current else None
            )
            pending = []
            for record in self.pending.values():
                pending.append(privacy_safe_execution_record(record))
            return {
                "current": current,
                "pending": pending,
                "records": list(self.records[-limit:]),
            }
