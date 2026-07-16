"""Deterministic edit-session state transitions with revision checks."""

from __future__ import annotations

import copy
import threading
from datetime import datetime
from enum import Enum


class EditStateTransitionError(RuntimeError):
    error_type = "validation_error"
    status = "state_conflict"


class EditStateConflict(EditStateTransitionError):
    pass


class EditSessionState(str, Enum):
    DISCONNECTED = "disconnected"
    ATTACHING = "attaching"
    READY = "ready"
    PREPARING = "preparing"
    PREPARED = "prepared"
    APPROVAL_REQUIRED = "approval_required"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMMITTED = "committed"
    STALE_CONTEXT = "stale_context"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


ALLOWED_TRANSITIONS = {
    EditSessionState.DISCONNECTED: {EditSessionState.ATTACHING},
    EditSessionState.ATTACHING: {
        EditSessionState.READY,
        EditSessionState.FAILED,
        EditSessionState.DISCONNECTED,
    },
    EditSessionState.READY: {
        EditSessionState.PREPARING,
        EditSessionState.STALE_CONTEXT,
        EditSessionState.DISCONNECTED,
    },
    EditSessionState.PREPARING: {
        EditSessionState.PREPARED,
        EditSessionState.FAILED,
        EditSessionState.STALE_CONTEXT,
    },
    EditSessionState.PREPARED: {
        EditSessionState.APPROVAL_REQUIRED,
        EditSessionState.EXECUTING,
        EditSessionState.READY,
        EditSessionState.STALE_CONTEXT,
    },
    EditSessionState.APPROVAL_REQUIRED: {
        EditSessionState.EXECUTING,
        EditSessionState.READY,
        EditSessionState.STALE_CONTEXT,
    },
    EditSessionState.EXECUTING: {
        EditSessionState.VERIFYING,
        EditSessionState.ROLLED_BACK,
        EditSessionState.FAILED,
    },
    EditSessionState.VERIFYING: {
        EditSessionState.COMMITTED,
        EditSessionState.ROLLED_BACK,
        EditSessionState.FAILED,
    },
    EditSessionState.COMMITTED: {
        EditSessionState.READY,
        EditSessionState.ROLLED_BACK,
    },
    EditSessionState.STALE_CONTEXT: {
        EditSessionState.READY,
        EditSessionState.DISCONNECTED,
    },
    EditSessionState.FAILED: {
        EditSessionState.READY,
        EditSessionState.DISCONNECTED,
    },
    EditSessionState.ROLLED_BACK: {EditSessionState.READY},
}


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class EditSessionStateMachine:
    """Thread-safe state holder; live COM values never enter its history."""

    def __init__(self, history_limit=100):
        self._lock = threading.RLock()
        self._state = EditSessionState.DISCONNECTED
        self._revision = 0
        self._active_action_id = None
        self._last_error = None
        self._history = []
        self._history_limit = max(10, min(int(history_limit), 1000))

    @property
    def state(self) -> EditSessionState:
        with self._lock:
            return self._state

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def require_state(self, *allowed) -> None:
        normalized = {
            value if isinstance(value, EditSessionState) else EditSessionState(value)
            for value in allowed
        }
        with self._lock:
            if self._state not in normalized:
                expected = ", ".join(sorted(item.value for item in normalized))
                raise EditStateConflict(
                    f"현재 편집 상태는 {self._state.value}이며 필요한 상태는 {expected}입니다."
                )

    def transition(
        self,
        next_state,
        *,
        reason="",
        action_id=None,
        expected_revision=None,
        error=None,
    ) -> dict:
        target = (
            next_state
            if isinstance(next_state, EditSessionState)
            else EditSessionState(next_state)
        )
        with self._lock:
            if expected_revision is not None and int(expected_revision) != self._revision:
                raise EditStateConflict(
                    "편집 상태 revision이 바뀌어 오래된 상태 전이를 거부했습니다."
                )
            if target not in ALLOWED_TRANSITIONS[self._state]:
                raise EditStateTransitionError(
                    f"허용되지 않는 편집 상태 전이입니다: {self._state.value} → {target.value}"
                )
            previous = self._state
            self._state = target
            self._revision += 1
            if action_id:
                self._active_action_id = str(action_id)
            if target in {EditSessionState.READY, EditSessionState.DISCONNECTED}:
                self._active_action_id = None
            self._last_error = str(error)[:1000] if error else None
            record = {
                "revision": self._revision,
                "from": previous.value,
                "to": target.value,
                "reason": str(reason or "")[:500],
                "action_id": self._active_action_id,
                "error": self._last_error,
                "at": _timestamp(),
            }
            self._history.append(record)
            del self._history[:-self._history_limit]
            return copy.deepcopy(record)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "revision": self._revision,
                "active_action_id": self._active_action_id,
                "last_error": self._last_error,
                "history": copy.deepcopy(self._history),
            }
