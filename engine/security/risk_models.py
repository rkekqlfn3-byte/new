"""Structured dynamic-code risk decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


SAFE = "safe"
CONFIRMATION_REQUIRED = "confirmation_required"
BLOCKED = "blocked"


def _stable_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RiskFinding:
    code: str
    category: str
    disposition: str
    message: str
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "disposition": self.disposition,
            "message": self.message,
            "line": self.line,
        }


@dataclass(frozen=True)
class DynamicCodePreflightResult:
    status: str
    code_sha256: str
    findings: tuple[RiskFinding, ...] = field(default_factory=tuple)
    policy_version: int = 1

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED

    @property
    def requires_confirmation(self) -> bool:
        return self.status == CONFIRMATION_REQUIRED

    def approval_fingerprint(self, argument=None, context=None) -> str:
        return _stable_digest({
            "policy_version": self.policy_version,
            "code_sha256": self.code_sha256,
            "argument_sha256": _stable_digest(argument),
            "context_sha256": _stable_digest(context or {}),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "policy_version": self.policy_version,
            "code_sha256": self.code_sha256,
            "findings": [item.to_dict() for item in self.findings],
        }
