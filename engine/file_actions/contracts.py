"""Serializable contracts for approved local file transformations."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

FILE_ACTION_CONTRACT_SCHEMA_VERSION = 1
MAX_FILE_ACTION_JSON_BYTES = 256 * 1024
MAX_FILE_ACTION_PAGES = 100_000
SUPPORTED_FILE_ACTIONS = frozenset({"extract_pages", "merge_documents", "rotate_pages"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FINGERPRINT = re.compile(r"^[A-Fa-f0-9]{64}$")


class FileActionContractError(ValueError):
    """The prepared local file action violates the execution boundary."""


def _timestamp(value: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise FileActionContractError("File action time must use ISO-8601 format.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FileActionContractError("File action time must include a timezone.")
    return parsed.isoformat(timespec="seconds")


def _identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise FileActionContractError(f"{label} has an invalid format.")
    return text


def _fingerprint(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _FINGERPRINT.fullmatch(text):
        raise FileActionContractError(f"{label} must be a SHA-256 fingerprint.")
    return text


def _json_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FileActionContractError(f"{label} must be an object.")
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FileActionContractError(f"{label} allows JSON values only.") from error
    if len(encoded) > MAX_FILE_ACTION_JSON_BYTES:
        raise FileActionContractError(f"{label} exceeds the safe size limit.")
    return json.loads(encoded.decode("utf-8"))


def _pdf_output_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text or "\x00" in text or len(text) > 32_767:
        raise FileActionContractError("PDF output path is invalid.")
    expanded = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(expanded) or expanded.startswith(("\\\\", "//")):
        raise FileActionContractError("PDF output path must be an absolute local path.")
    path = os.path.abspath(expanded)
    if Path(path).suffix.casefold() != ".pdf":
        raise FileActionContractError("PDF output path must have a .pdf extension.")
    return path


def _state_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PageSelection:
    """Ordered pages selected from one source index."""

    source_index: int
    page_numbers: tuple[int, ...]

    def __post_init__(self):
        if isinstance(self.source_index, bool):
            raise FileActionContractError("PDF source index must be an integer.")
        try:
            source_index = int(self.source_index)
            page_numbers = tuple(int(item) for item in self.page_numbers)
        except (TypeError, ValueError, OverflowError) as error:
            raise FileActionContractError("PDF page selection format is invalid.") from error
        if source_index < 0:
            raise FileActionContractError("PDF source index must be zero or greater.")
        if not page_numbers or any(
            number < 1 or number > MAX_FILE_ACTION_PAGES for number in page_numbers
        ):
            raise FileActionContractError("PDF page number is outside the allowed range.")
        object.__setattr__(self, "source_index", source_index)
        object.__setattr__(self, "page_numbers", page_numbers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_index": self.source_index,
            "page_numbers": list(self.page_numbers),
        }


@dataclass(frozen=True)
class PreparedFileAction:
    """A prepared, approved-before-write local file transformation."""

    action_id: str
    request_id: str
    operation: str
    source_fingerprints: tuple[str, ...]
    output_path: str
    page_selection: tuple[PageSelection, ...]
    current_state: dict[str, Any]
    expected_state: dict[str, Any]
    destructive: bool
    reversible: bool
    requires_approval: bool
    verification_plan: dict[str, Any]
    rollback_plan: dict[str, Any]
    prepared_at: str = field(default_factory=_timestamp)
    schema_version: int = FILE_ACTION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "action_id", _identifier(self.action_id, "file action ID"))
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request ID"))
        operation = str(self.operation or "").strip().casefold()
        if operation not in SUPPORTED_FILE_ACTIONS:
            raise FileActionContractError("Unsupported file action operation.")
        fingerprints = tuple(
            _fingerprint(item, "source file fingerprint") for item in self.source_fingerprints
        )
        if not fingerprints:
            raise FileActionContractError("File action requires at least one source.")
        if operation == "merge_documents" and len(fingerprints) < 2:
            raise FileActionContractError("PDF merge requires at least two sources.")
        if operation != "merge_documents" and len(fingerprints) != 1:
            raise FileActionContractError("This PDF action supports exactly one source.")
        try:
            selections = tuple(self.page_selection)
        except TypeError as error:
            raise FileActionContractError("PDF page selection must be a sequence.") from error
        if any(not isinstance(item, PageSelection) for item in selections):
            raise FileActionContractError("PDF page selection contract is invalid.")
        if any(item.source_index >= len(fingerprints) for item in selections):
            raise FileActionContractError("PDF page selection source index is out of range.")
        if type(self.destructive) is not bool or type(self.reversible) is not bool:
            raise FileActionContractError("File action impact flags must be booleans.")
        if type(self.requires_approval) is not bool or not self.requires_approval:
            raise FileActionContractError("PDF file actions require user approval.")
        verification = _json_mapping(self.verification_plan, "verification plan")
        rollback = _json_mapping(self.rollback_plan, "rollback plan")
        if not str(verification.get("method") or "").strip():
            raise FileActionContractError("Verification plan requires a method.")
        if not str(rollback.get("strategy") or "").strip():
            raise FileActionContractError("Rollback plan requires a strategy.")
        if int(self.schema_version) != FILE_ACTION_CONTRACT_SCHEMA_VERSION:
            raise FileActionContractError("Unsupported file action contract schema version.")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "source_fingerprints", fingerprints)
        object.__setattr__(self, "output_path", _pdf_output_path(self.output_path))
        object.__setattr__(self, "page_selection", selections)
        object.__setattr__(
            self, "current_state", _json_mapping(self.current_state, "current state")
        )
        object.__setattr__(
            self, "expected_state", _json_mapping(self.expected_state, "expected state")
        )
        object.__setattr__(self, "verification_plan", verification)
        object.__setattr__(self, "rollback_plan", rollback)
        object.__setattr__(self, "prepared_at", _timestamp(self.prepared_at))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_fingerprints"] = list(self.source_fingerprints)
        value["page_selection"] = [item.to_dict() for item in self.page_selection]
        return copy.deepcopy(value)

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "request_id": self.request_id,
            "operation": self.operation,
            "source_fingerprints": list(self.source_fingerprints),
            "output_path_fingerprint": hashlib.sha256(
                os.path.normcase(self.output_path).encode("utf-8")
            ).hexdigest(),
            "output_extension": ".pdf",
            "page_selection": [item.to_dict() for item in self.page_selection],
            "current_state_fingerprint": _state_fingerprint(self.current_state),
            "expected_state_fingerprint": _state_fingerprint(self.expected_state),
            "destructive": self.destructive,
            "reversible": self.reversible,
            "requires_approval": self.requires_approval,
            "verification_method": self.verification_plan["method"],
            "rollback_strategy": self.rollback_plan["strategy"],
            "prepared_at": self.prepared_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedFileAction":
        if not isinstance(value, Mapping):
            raise FileActionContractError("Prepared file action must be an object.")
        data = dict(value)
        data["source_fingerprints"] = tuple(data.get("source_fingerprints") or ())
        selections = data.get("page_selection") or ()
        data["page_selection"] = tuple(
            item if isinstance(item, PageSelection) else PageSelection(**dict(item))
            for item in selections
        )
        return cls(**data)
