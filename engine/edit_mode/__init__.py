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

__all__ = [
    "ActionScope",
    "EditAdapter",
    "EditApprovalRequired",
    "EditContextChanged",
    "EditContractError",
    "EditExecutionCoordinator",
    "EditExecutionError",
    "EditExecutionResult",
    "EditPreparedAction",
    "EditRequest",
    "EditSessionState",
    "EditSessionStateMachine",
    "EditStateConflict",
    "EditStateTransitionError",
    "EditVerificationError",
    "ModePermissionError",
    "RequestMode",
    "RiskLevel",
    "assert_action_allowed",
    "normalize_request_mode",
    "validate_edit_adapter",
]
