"""Backend permission matrix for conversation, question, command, and edit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from engine.edit_mode.contracts import RequestMode


class ModePermissionError(RuntimeError):
    error_type = "validation_error"
    status = "blocked"


class ActionScope(str, Enum):
    AI_RESPONSE = "ai_response"
    DOCUMENT_READ = "document_read"
    DOCUMENT_WRITE = "document_write"
    GLOBAL_ACTION = "global_action"
    DYNAMIC_CODE = "dynamic_code"


@dataclass(frozen=True)
class ModePermissions:
    ai_response: bool
    document_read: bool
    document_write: bool
    global_action: bool
    dynamic_code: bool


MODE_PERMISSIONS = {
    RequestMode.CONVERSATION: ModePermissions(True, False, False, False, False),
    RequestMode.QUESTION: ModePermissions(True, True, False, False, False),
    # Existing command-mode Excel/HWP functions remain available during the
    # staged migration. Their current prepare/verify boundary still applies.
    RequestMode.COMMAND: ModePermissions(True, True, True, True, True),
    RequestMode.EDIT: ModePermissions(True, True, True, False, False),
}


def normalize_request_mode(value) -> RequestMode:
    try:
        return value if isinstance(value, RequestMode) else RequestMode(str(value).casefold())
    except ValueError as error:
        raise ModePermissionError(f"지원하지 않는 요청 모드입니다: {value}") from error


def assert_action_allowed(mode, scope) -> None:
    request_mode = normalize_request_mode(mode)
    action_scope = scope if isinstance(scope, ActionScope) else ActionScope(scope)
    permissions = MODE_PERMISSIONS[request_mode]
    if not getattr(permissions, action_scope.value):
        labels = {
            ActionScope.DOCUMENT_READ: "문서 읽기",
            ActionScope.DOCUMENT_WRITE: "문서 변경",
            ActionScope.GLOBAL_ACTION: "컴퓨터 전체 작업",
            ActionScope.DYNAMIC_CODE: "동적 코드 실행",
            ActionScope.AI_RESPONSE: "AI 응답",
        }
        raise ModePermissionError(
            f"{request_mode.value} 모드에서는 {labels[action_scope]}을 실행할 수 없습니다."
        )
