"""Contracts, state, and execution boundaries for document edit mode."""

from engine.edit_mode.contracts import (
    EditAdapter,
    EditContractError,
    EditExecutionResult,
    EditPreparedAction,
    EditRequest,
    RequestMode,
    RiskLevel,
    validate_edit_adapter,
)
from engine.edit_mode.coordinator import (
    EditApprovalRequired,
    EditContextChanged,
    EditExecutionCoordinator,
    EditExecutionError,
    EditVerificationError,
)
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.intake import (
    APP_LABELS,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    EditAppUnavailable,
    EditDocumentAmbiguous,
    EditDocumentOpenTimeout,
    EditIntakeError,
    FileIntakeManager,
    UnsupportedEditDocument,
    app_type_for_path,
)
from engine.edit_mode.permissions import (
    ActionScope,
    ModePermissionError,
    assert_action_allowed,
    normalize_request_mode,
)
from engine.edit_mode.state_machine import (
    EditSessionState,
    EditSessionStateMachine,
    EditStateConflict,
    EditStateTransitionError,
)
from engine.edit_mode.session import (
    EditSession,
    EditSessionBusy,
    EditSessionError,
    EditSessionManager,
    EditSessionNotFound,
    EditSessionStale,
    canonical_document_path,
    document_identity_fingerprint,
)
from engine.edit_mode.window_layout import WindowLayoutManager

__all__ = [
    "ActionScope",
    "EditAdapter",
    "EditApprovalRequired",
    "EditContextChanged",
    "EditContractError",
    "EditAppUnavailable",
    "EditDocumentAmbiguous",
    "EditDocumentOpenTimeout",
    "EditExecutionCoordinator",
    "EditExecutionError",
    "EditExecutionResult",
    "EditPreparedAction",
    "EditRequest",
    "EditIntakeError",
    "EditModeController",
    "EditSession",
    "EditSessionBusy",
    "EditSessionError",
    "EditSessionManager",
    "EditSessionNotFound",
    "EditSessionState",
    "EditSessionStateMachine",
    "EditSessionStale",
    "EditStateConflict",
    "EditStateTransitionError",
    "EditVerificationError",
    "ModePermissionError",
    "RequestMode",
    "RiskLevel",
    "APP_LABELS",
    "SUPPORTED_DOCUMENT_EXTENSIONS",
    "FileIntakeManager",
    "UnsupportedEditDocument",
    "WindowLayoutManager",
    "app_type_for_path",
    "assert_action_allowed",
    "canonical_document_path",
    "document_identity_fingerprint",
    "normalize_request_mode",
    "validate_edit_adapter",
]
