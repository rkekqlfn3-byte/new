"""Bounded, content-free event model for the novice-facing activity view."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Mapping


USER_FEEDBACK_SCHEMA_VERSION = 1

EVENT_TYPES = frozenset({
    "request_received",
    "intent_resolved",
    "action_preparing",
    "action_started",
    "confirmation_required",
    "clarification_required",
    "verification_passed",
    "verification_failed",
    "action_completed",
    "action_failed",
    "rollback_started",
    "rollback_completed",
    "rollback_failed",
    "action_cancelled",
    "action_busy",
    "recovery_started",
    "recovery_completed",
    "recovery_failed",
})

SAFE_TARGET_FIELDS = frozenset({"sheet", "range"})
SAFE_DETAIL_FIELDS = frozenset({
    "error_type",
    "failed_step",
    "retryable",
    "reason",
    "status",
    "count",
    "attempt",
    "max_attempts",
    "confirmation_pending",
})
SAFE_UNDO_FIELDS = frozenset({
    "edit_session_id", "action_id", "document_fingerprint",
    "context_fingerprint",
})
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_FINGERPRINT_RE = re.compile(r"^[A-Fa-f0-9]{16,128}$")


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_target(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    target = {}
    for key in SAFE_TARGET_FIELDS:
        text = _bounded_text(value.get(key), 120)
        if text:
            target[key] = text
    return target


def _safe_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    details: dict[str, Any] = {}
    for key in SAFE_DETAIL_FIELDS:
        item = value.get(key)
        if isinstance(item, bool):
            details[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            details[key] = max(-1, min(item, 1_000_000))
        elif item is not None:
            text = _bounded_text(item, 80)
            if text:
                details[key] = text
    return details


def _safe_undo(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    undo = {}
    for key in SAFE_UNDO_FIELDS:
        text = _bounded_text(value.get(key), 128)
        pattern = (
            _SAFE_FINGERPRINT_RE
            if key.endswith("fingerprint") else _SAFE_IDENTIFIER_RE
        )
        if text and pattern.fullmatch(text):
            undo[key] = text
    return undo


@dataclass(frozen=True)
class UserFeedbackEvent:
    """One display event with no document contents, paths, or user utterance."""

    event_type: str
    stage: str = "execution"
    app: str = ""
    action: str = "command"
    execution_id: str = ""
    target: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    success: bool | None = None
    verified: bool = False
    undo_available: bool = False
    undo: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self):
        if self.event_type not in EVENT_TYPES:
            raise ValueError("지원하지 않는 사용자 실행 이벤트입니다.")
        if self.success is not None and type(self.success) is not bool:
            raise ValueError("사용자 실행 이벤트의 성공 여부가 올바르지 않습니다.")
        if type(self.verified) is not bool or type(self.undo_available) is not bool:
            raise ValueError("사용자 실행 이벤트의 검증 계약이 올바르지 않습니다.")
        object.__setattr__(self, "stage", _bounded_text(self.stage, 40) or "execution")
        object.__setattr__(self, "app", _bounded_text(self.app, 40))
        object.__setattr__(self, "action", _bounded_text(self.action, 80) or "command")
        object.__setattr__(self, "execution_id", _bounded_text(self.execution_id, 80))
        object.__setattr__(self, "target", _safe_target(self.target))
        object.__setattr__(self, "details", _safe_details(self.details))
        object.__setattr__(self, "undo", _safe_undo(self.undo))
        if self.undo_available and not all(
            self.undo.get(key) for key in SAFE_UNDO_FIELDS
        ):
            object.__setattr__(self, "undo_available", False)
        if not self.undo_available:
            object.__setattr__(self, "undo", {})
        object.__setattr__(
            self,
            "created_at",
            _bounded_text(self.created_at, 40)
            or datetime.now().isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": USER_FEEDBACK_SCHEMA_VERSION,
            "event_type": self.event_type,
            "stage": self.stage,
            "app": self.app,
            "action": self.action,
            "execution_id": self.execution_id,
            "target": dict(self.target),
            "details": dict(self.details),
            "success": self.success,
            "verified": self.verified,
            "undo_available": self.undo_available,
            "undo": dict(self.undo),
            "created_at": self.created_at,
        }
