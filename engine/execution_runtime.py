"""Execution cancellation, structured results, and bounded diagnostics."""

import threading
import time
import uuid
from datetime import datetime

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json


DIAGNOSTICS_PATH = user_data_path("execution_diagnostics.json")


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
    def __init__(self, diagnostics_path=None, max_records=100):
        self.diagnostics_path = diagnostics_path or DIAGNOSTICS_PATH
        self.max_records = max_records
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self.current = None
        loaded = safe_read_json(self.diagnostics_path, {"records": []})
        records = loaded.get("records", []) if isinstance(loaded, dict) else []
        self.records = list(records)[-max_records:] if isinstance(records, list) else []
        # Paused confirmations are process-local and intentionally never loaded
        # from diagnostics after a restart.
        self.pending = {}

    def begin(self, label="command", metadata=None):
        with self._lock:
            # Reject before clearing the cancel event so a rejected second
            # command never wipes the running command's pending cancellation.
            if self.current is not None:
                raise ExecutionBusyError(
                    "이미 실행 중인 작업이 있어 새 명령을 시작할 수 없습니다. "
                    "현재 작업이 끝난 뒤 다시 시도해 주세요."
                )
            self._cancel_event.clear()
            execution_id = uuid.uuid4().hex
            self.current = {
                "execution_id": execution_id,
                "label": str(label)[:200],
                "status": "running",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "started_monotonic": time.monotonic(),
                "metadata": dict(metadata or {}),
                "events": [],
            }
            return execution_id

    def event(self, action, status="running", details=None):
        with self._lock:
            if not self.current:
                return
            self.current["events"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": str(action),
                "status": str(status),
                "details": details if isinstance(details, dict) else {},
            })

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

    def finish(self, success, status=None, response="", error="", extra=None):
        with self._lock:
            if not self.current:
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
            self.records.append(record)
            self.records = self.records[-self.max_records:]
            self.current = None
            self._cancel_event.clear()
            try:
                atomic_write_json(self.diagnostics_path, {"records": self.records})
            except OSError:
                pass
            return record

    def pause_for_confirmation(
        self, confirmation_id, response="", extra=None
    ):
        """Move the running execution into an in-memory waiting state."""
        with self._lock:
            if not self.current:
                return None
            record = self.current
            execution_id = record["execution_id"]
            record["status"] = "confirmation_required"
            record["paused_at"] = datetime.now().isoformat(timespec="seconds")
            record["confirmation_id"] = str(confirmation_id or "")
            record["response"] = str(response or "")[:1000]
            record["events"].append({
                "time": datetime.now().isoformat(timespec="seconds"),
                "action": "confirmation",
                "status": "confirmation_required",
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
            record = self.pending.pop(str(execution_id or ""), None)
            if not record:
                return False
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
            current = dict(self.current) if self.current else None
            if current:
                current.pop("started_monotonic", None)
            pending = []
            for record in self.pending.values():
                item = dict(record)
                item.pop("started_monotonic", None)
                pending.append(item)
            return {
                "current": current,
                "pending": pending,
                "records": list(self.records[-limit:]),
            }
