"""Privacy-bounded incident collection and deterministic self-diagnosis."""

from engine.diagnostics.self_diagnosis import (
    DiagnosticIncidentError,
    DiagnosticIncidentManager,
    collect_environment_snapshot,
    privacy_safe_execution_record,
)

__all__ = [
    "DiagnosticIncidentError",
    "DiagnosticIncidentManager",
    "collect_environment_snapshot",
    "privacy_safe_execution_record",
]
