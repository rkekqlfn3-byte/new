"""Serializable edit-mode contracts that never retain live COM objects."""

from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

EDIT_CONTRACT_SCHEMA_VERSION = 2
MAX_EDIT_TEXT_CHARS = 10_000
MAX_SERIALIZED_ACTION_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_OPERATION = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_FINGERPRINT = re.compile(r"^[A-Fa-f0-9]{16,128}$")


class EditContractError(ValueError):
    """The request or prepared action violates the edit-mode boundary."""

    error_type = "validation_error"
    status = "blocked"


class RequestMode(str, Enum):
    CONVERSATION = "conversation"
    QUESTION = "question"
    COMMAND = "command"
    EDIT = "edit"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def parse(cls, value: str | "RiskLevel") -> "RiskLevel":
        try:
            return value if isinstance(value, cls) else cls(str(value).casefold())
        except ValueError as error:
            raise EditContractError(f"지원하지 않는 편집 위험도입니다: {value}") from error


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _latest_text(value) -> str:
    if not isinstance(value, list):
        return str(value or "").strip()
    for item in reversed(value):
        if isinstance(item, Mapping) and str(item.get("role", "")) == "user":
            return str(item.get("content", "") or "").strip()
    return ""


def _identifier(value, label) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise EditContractError(f"{label} 형식이 올바르지 않습니다.")
    return text


def _fingerprint(value) -> str:
    text = str(value or "").strip().upper()
    if not _FINGERPRINT.fullmatch(text):
        raise EditContractError("문서 fingerprint 형식이 올바르지 않습니다.")
    return text


def _json_copy(value, label):
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise EditContractError(f"{label}에는 JSON 값만 사용할 수 있습니다.") from error
    if len(encoded) > MAX_SERIALIZED_ACTION_BYTES:
        raise EditContractError(f"{label} 직렬화 크기가 안전 한도를 넘었습니다.")
    return json.loads(encoded.decode("utf-8"))


def _mapping(value, label) -> dict:
    if not isinstance(value, Mapping):
        raise EditContractError(f"{label}은 객체여야 합니다.")
    return dict(value)


def _precondition_values(value) -> tuple[dict, ...]:
    if isinstance(value, (str, bytes)):
        raise EditContractError("실행 전 조건은 객체 배열이어야 합니다.")
    try:
        return tuple(_mapping(item, "실행 전 조건") for item in value)
    except TypeError as error:
        raise EditContractError("실행 전 조건은 객체 배열이어야 합니다.") from error


@dataclass(frozen=True)
class EditRequest:
    """One edit request bound to an already connected document session."""

    text: str
    edit_session_id: str
    document_fingerprint: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    mode: str = RequestMode.EDIT.value
    context_fingerprint: str | None = None
    expected_undo_action_id: str | None = None

    def __post_init__(self):
        text = str(self.text or "").strip()
        if not text or len(text) > MAX_EDIT_TEXT_CHARS or "\x00" in text:
            raise EditContractError("편집 요청은 1~10,000자의 유효한 텍스트여야 합니다.")
        if str(self.mode) != RequestMode.EDIT.value:
            raise EditContractError("EditRequest의 mode는 edit여야 합니다.")
        object.__setattr__(self, "text", text)
        object.__setattr__(
            self, "edit_session_id", _identifier(self.edit_session_id, "편집 세션 ID")
        )
        object.__setattr__(self, "request_id", _identifier(self.request_id, "요청 ID"))
        object.__setattr__(
            self, "document_fingerprint", _fingerprint(self.document_fingerprint)
        )
        if self.context_fingerprint is not None:
            object.__setattr__(
                self, "context_fingerprint", _fingerprint(self.context_fingerprint)
            )
        if self.expected_undo_action_id is not None:
            object.__setattr__(
                self,
                "expected_undo_action_id",
                _identifier(self.expected_undo_action_id, "되돌리기 작업 ID"),
            )

    @classmethod
    def from_input(cls, user_input, edit_context=None) -> "EditRequest":
        context = edit_context if isinstance(edit_context, Mapping) else {}
        if not context.get("context_fingerprint"):
            raise EditContractError(
                "편집 요청 전에 현재 선택 문맥을 다시 확인해야 합니다."
            )
        return cls(
            text=_latest_text(user_input),
            edit_session_id=context.get("edit_session_id"),
            document_fingerprint=context.get("document_fingerprint"),
            request_id=context.get("request_id") or uuid.uuid4().hex,
            context_fingerprint=context.get("context_fingerprint"),
            expected_undo_action_id=context.get("expected_undo_action_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(asdict(self))


@dataclass(frozen=True)
class EditPreparedAction:
    """Allowlisted, COM-free action prepared from a specific document state."""

    action_id: str
    request_id: str
    edit_session_id: str
    app_type: str
    operation: str
    target: dict[str, Any]
    arguments: dict[str, Any]
    preconditions: tuple[dict[str, Any], ...]
    risk_level: RiskLevel | str
    requires_approval: bool
    verification_plan: dict[str, Any]
    rollback_plan: dict[str, Any]
    context_fingerprint: str
    prepared_at: str = field(default_factory=_timestamp)
    schema_version: int = EDIT_CONTRACT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "action_id", _identifier(self.action_id, "작업 ID"))
        object.__setattr__(self, "request_id", _identifier(self.request_id, "요청 ID"))
        object.__setattr__(
            self, "edit_session_id", _identifier(self.edit_session_id, "편집 세션 ID")
        )
        object.__setattr__(self, "app_type", _identifier(self.app_type, "앱 유형").casefold())
        operation = str(self.operation or "").strip().casefold()
        if not _OPERATION.fullmatch(operation):
            raise EditContractError("편집 operation은 허용 목록용 식별자여야 합니다.")
        object.__setattr__(self, "operation", operation)
        object.__setattr__(
            self, "context_fingerprint", _fingerprint(self.context_fingerprint)
        )
        risk_level = RiskLevel.parse(self.risk_level)
        object.__setattr__(self, "risk_level", risk_level)
        if risk_level is RiskLevel.HIGH and not self.requires_approval:
            raise EditContractError("고위험 편집 작업은 사용자 승인이 필수입니다.")
        if int(self.schema_version) != EDIT_CONTRACT_SCHEMA_VERSION:
            raise EditContractError("지원하지 않는 편집 계약 버전입니다.")
        object.__setattr__(
            self, "target", _json_copy(_mapping(self.target, "편집 대상"), "편집 대상")
        )
        object.__setattr__(
            self,
            "arguments",
            _json_copy(_mapping(self.arguments, "편집 인자"), "편집 인자"),
        )
        preconditions = _precondition_values(self.preconditions)
        object.__setattr__(
            self, "preconditions", tuple(_json_copy(preconditions, "실행 전 조건"))
        )
        verification_plan = _json_copy(
            _mapping(self.verification_plan, "검증 계획"), "검증 계획"
        )
        rollback_plan = _json_copy(
            _mapping(self.rollback_plan, "복구 계획"), "복구 계획"
        )
        if not str(verification_plan.get("method", "")).strip():
            raise EditContractError("검증 계획에는 method가 필요합니다.")
        if not str(rollback_plan.get("strategy", "")).strip():
            raise EditContractError("복구 계획에는 strategy가 필요합니다.")
        object.__setattr__(self, "verification_plan", verification_plan)
        object.__setattr__(self, "rollback_plan", rollback_plan)
        object.__setattr__(
            self,
            "metadata",
            _json_copy(_mapping(self.metadata, "편집 메타데이터"), "편집 메타데이터"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk_level"] = self.risk_level.value
        result["preconditions"] = [dict(item) for item in self.preconditions]
        return copy.deepcopy(result)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EditPreparedAction":
        if not isinstance(value, Mapping):
            raise EditContractError("준비된 편집 작업은 객체여야 합니다.")
        required = {
            "action_id",
            "request_id",
            "edit_session_id",
            "app_type",
            "operation",
            "target",
            "arguments",
            "preconditions",
            "risk_level",
            "requires_approval",
            "verification_plan",
            "rollback_plan",
            "context_fingerprint",
        }
        missing = sorted(required - set(value))
        if missing:
            raise EditContractError(
                "준비된 편집 작업 정보가 불완전합니다: " + ", ".join(missing)
            )
        return cls(
            action_id=value["action_id"],
            request_id=value["request_id"],
            edit_session_id=value["edit_session_id"],
            app_type=value["app_type"],
            operation=value["operation"],
            target=_mapping(value["target"], "편집 대상"),
            arguments=_mapping(value["arguments"], "편집 인자"),
            preconditions=_precondition_values(value["preconditions"]),
            risk_level=value["risk_level"],
            requires_approval=bool(value["requires_approval"]),
            verification_plan=_mapping(value["verification_plan"], "검증 계획"),
            rollback_plan=_mapping(value["rollback_plan"], "복구 계획"),
            context_fingerprint=value["context_fingerprint"],
            prepared_at=str(value.get("prepared_at") or _timestamp()),
            schema_version=int(
                value.get("schema_version", EDIT_CONTRACT_SCHEMA_VERSION)
            ),
            metadata=_mapping(value.get("metadata", {}), "편집 메타데이터"),
        )


@dataclass(frozen=True)
class EditExecutionResult:
    action_id: str
    changed: bool
    verified: bool
    observations: dict[str, Any]
    completed_at: str = field(default_factory=_timestamp)

    def __post_init__(self):
        object.__setattr__(self, "action_id", _identifier(self.action_id, "작업 ID"))
        object.__setattr__(
            self,
            "observations",
            _json_copy(_mapping(self.observations, "실행 관찰값"), "실행 관찰값"),
        )

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_value(
        cls, action_id: str, value: Mapping[str, Any], verified: bool
    ) -> "EditExecutionResult":
        if not isinstance(value, Mapping):
            raise EditContractError("편집 실행 결과는 객체여야 합니다.")
        return cls(
            action_id=action_id,
            changed=bool(value.get("changed", False)),
            verified=bool(verified),
            observations=dict(value),
        )


@runtime_checkable
class EditAdapter(Protocol):
    """The only execution surface available to the edit coordinator."""

    app_type: str
    supported_operations: frozenset[str]

    def get_context(self) -> Mapping[str, Any]: ...

    def fingerprint(self, context: Mapping[str, Any]) -> str: ...

    def prepare(
        self, request: EditRequest, context: Mapping[str, Any]
    ) -> EditPreparedAction: ...

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]: ...

    def verify(
        self,
        prepared_action: EditPreparedAction,
        result: Mapping[str, Any],
    ) -> bool: ...

    def rollback(self, prepared_action: EditPreparedAction) -> bool: ...


def validate_edit_adapter(adapter) -> frozenset[str]:
    """Validate the trusted adapter surface before it can execute edits."""
    for name in ("get_context", "fingerprint", "prepare", "execute", "verify", "rollback"):
        if not callable(getattr(adapter, name, None)):
            raise EditContractError(f"편집 어댑터에 {name} 메서드가 없습니다.")
    _identifier(getattr(adapter, "app_type", None), "앱 유형")
    try:
        operations = frozenset(str(item).casefold() for item in adapter.supported_operations)
    except (AttributeError, TypeError) as error:
        raise EditContractError("편집 어댑터의 지원 작업 목록이 없습니다.") from error
    if not operations or any(not _OPERATION.fullmatch(item) for item in operations):
        raise EditContractError("편집 어댑터의 지원 작업 식별자가 올바르지 않습니다.")
    return operations
