"""One-attempt recovery proof for work that has not started executing."""

from __future__ import annotations

import hashlib
import json
import re
import uuid


RECOVERY_CONTRACT_SCHEMA_VERSION = 1
_SAFE_OUTCOMES = frozenset({"recovered", "not_found", "unavailable"})


def recovery_target_signature(intent, candidates) -> str:
    """Hash one requested target without retaining natural-language content."""
    normalized_intent = re.sub(
        r"[^0-9a-z_]", "", str(intent or "").strip().casefold()
    )[:50]
    normalized_candidates = sorted({
        re.sub(r"[^0-9a-z가-힣]", "", str(item or "").casefold())[:100]
        for item in (candidates or ())
        if re.sub(r"[^0-9a-z가-힣]", "", str(item or "").casefold())
    })
    payload = json.dumps(
        {
            "intent": normalized_intent,
            "candidate_hashes": [
                hashlib.sha256(item.encode("utf-8")).hexdigest().upper()
                for item in normalized_candidates
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


class PreExecutionRecoveryContract:
    """Permit at most one recovery while target identity remains unchanged."""

    def __init__(
        self,
        *,
        strategy: str,
        target_kind: str,
        target_signature: str,
        retry_limit: int = 1,
    ):
        self.recovery_id = uuid.uuid4().hex
        self.strategy = re.sub(
            r"[^0-9a-z_]", "", str(strategy or "").casefold()
        )[:80] or "unknown"
        self.target_kind = re.sub(
            r"[^0-9a-z_]", "", str(target_kind or "").casefold()
        )[:50] or "unknown"
        signature = str(target_signature or "").strip().upper()
        self.target_signature = (
            signature
            if re.fullmatch(r"[A-F0-9]{64}", signature)
            else hashlib.sha256(signature.encode("utf-8")).hexdigest().upper()
        )
        # Automatic recovery is deliberately one-shot. A broader caller value
        # cannot relax this invariant.
        try:
            requested_limit = int(retry_limit or 0)
        except (TypeError, ValueError, OverflowError):
            requested_limit = 0
        self.retry_limit = 1 if requested_limit >= 1 else 0
        self.retry_count = 0
        self.attempted = False
        self.execution_started = False
        self.target_unchanged = None
        self.target_resolved = False
        self.outcome = "not_attempted"

    def mark_execution_started(self) -> None:
        self.execution_started = True
        if not self.attempted:
            self.outcome = "blocked_after_execution"

    def begin(self, current_target_signature: str) -> bool:
        """Reserve the only attempt after checking phase and target identity."""
        signature = str(current_target_signature or "").strip().upper()
        if self.execution_started:
            self.outcome = "blocked_after_execution"
            self.target_unchanged = signature == self.target_signature
            return False
        if signature != self.target_signature:
            self.outcome = "target_changed"
            self.target_unchanged = False
            return False
        if self.retry_count >= self.retry_limit:
            self.outcome = "retry_exhausted"
            self.target_unchanged = True
            return False
        self.attempted = True
        self.retry_count += 1
        self.target_unchanged = True
        self.outcome = "retrying"
        return True

    def complete(
        self,
        outcome: str,
        *,
        current_target_signature: str,
        target_resolved: bool,
    ) -> str:
        """Close the attempt without permitting a changed or executed target."""
        signature = str(current_target_signature or "").strip().upper()
        if not self.attempted:
            self.outcome = "invalid_contract_state"
            self.target_resolved = False
            return self.outcome
        if self.execution_started:
            self.outcome = "blocked_after_execution"
            self.target_resolved = False
            return self.outcome
        if signature != self.target_signature:
            self.outcome = "target_changed"
            self.target_unchanged = False
            self.target_resolved = False
            return self.outcome
        self.target_unchanged = True
        requested = str(outcome or "").casefold()
        if requested not in _SAFE_OUTCOMES:
            requested = "unavailable"
        self.target_resolved = bool(target_resolved)
        if requested == "recovered" and not self.target_resolved:
            requested = "not_found"
        self.outcome = requested
        return self.outcome

    def to_dict(self) -> dict:
        """Return a content-free proof safe for command results and diagnostics."""
        return {
            "schema_version": RECOVERY_CONTRACT_SCHEMA_VERSION,
            "recovery_id": self.recovery_id,
            "attempted": self.attempted,
            "strategy": self.strategy,
            "phase": "pre_execution",
            "target_kind": self.target_kind,
            "target_signature": self.target_signature,
            "execution_started": self.execution_started,
            "target_unchanged": self.target_unchanged,
            "target_resolved": self.target_resolved,
            "retry_count": self.retry_count,
            "retry_limit": self.retry_limit,
            "outcome": self.outcome,
        }
