"""Serializable contracts shared by native application adapters."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any


class AppActionError(RuntimeError):
    """Base error with fields understood by Jarvis' execution boundary."""

    error_type = "execution_error"
    status = "failed"
    retryable = False


class AppActionUnavailable(AppActionError):
    error_type = "target_not_found"
    # Raised while locating the target, before any document is modified.
    state_changed = False


class AppActionBusy(AppActionError):
    error_type = "environment_error"
    # Raised before acquiring the app, so nothing external changed yet.
    state_changed = False


class AppActionBlocked(AppActionError):
    error_type = "validation_error"
    status = "blocked"


class AppActionAmbiguousTarget(AppActionBlocked):
    """A safe, serializable target choice is required before preparation."""

    status = "confirmation_required"

    def __init__(self, message, candidates=None, target_name=None):
        super().__init__(message)
        self.candidates = [str(item) for item in (candidates or [])]
        self.target_name = str(target_name or "")


class AppActionContextChanged(AppActionError):
    error_type = "validation_error"
    status = "context_changed"


class AppActionVerificationError(AppActionError):
    error_type = "verification_error"


@dataclass(frozen=True)
class PreparedAction:
    """A COM-free snapshot of one action prepared from live application state."""

    app: str
    operation: str
    document_id: str
    workbook_name: str
    sheet: str
    target: str
    params: dict[str, Any]
    current_state: dict[str, Any]
    estimated_changes: int
    destructive: bool
    reversible: bool
    verification_method: str
    context_fingerprint: str
    prepared_at: str
    noop: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PreparedAction":
        if not isinstance(value, dict):
            raise AppActionBlocked("준비된 앱 작업 정보가 올바르지 않습니다.")
        required = {
            "app", "operation", "document_id", "workbook_name", "sheet",
            "target", "params", "current_state", "estimated_changes",
            "destructive", "reversible", "verification_method",
            "context_fingerprint", "prepared_at",
        }
        missing = sorted(required - set(value))
        if missing:
            raise AppActionBlocked(
                "준비된 앱 작업 정보가 불완전합니다: " + ", ".join(missing)
            )
        return cls(
            app=str(value["app"]),
            operation=str(value["operation"]),
            document_id=str(value["document_id"]),
            workbook_name=str(value["workbook_name"]),
            sheet=str(value["sheet"]),
            target=str(value["target"]),
            params=copy.deepcopy(value["params"]),
            current_state=copy.deepcopy(value["current_state"]),
            estimated_changes=int(value["estimated_changes"]),
            destructive=bool(value["destructive"]),
            reversible=bool(value["reversible"]),
            verification_method=str(value["verification_method"]),
            context_fingerprint=str(value["context_fingerprint"]),
            prepared_at=str(value["prepared_at"]),
            noop=bool(value.get("noop", False)),
            metadata=copy.deepcopy(value.get("metadata", {})),
        )
