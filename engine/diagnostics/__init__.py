"""Privacy-bounded incident collection and deterministic self-diagnosis."""

from engine.diagnostics.failure_triage import (
    DeveloperIssueRegistry,
    DeterministicFailureClassifier,
    EvidenceCode,
    FailureCategory,
    FailureOwner,
    FailureTriageResult,
    normalize_triage,
)
from engine.diagnostics.self_diagnosis import (
    DiagnosticIncidentError,
    DiagnosticIncidentManager,
    collect_environment_snapshot,
    privacy_safe_execution_record,
)

__all__ = [
    "DeveloperIssueRegistry",
    "DeterministicFailureClassifier",
    "DiagnosticIncidentError",
    "DiagnosticIncidentManager",
    "EvidenceCode",
    "FailureCategory",
    "FailureOwner",
    "FailureTriageResult",
    "collect_environment_snapshot",
    "normalize_triage",
    "privacy_safe_execution_record",
]
