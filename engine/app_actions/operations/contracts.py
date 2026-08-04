"""Registration contract for one native application operation.

Adapters own the COM lifecycle, the active-document context and the shared
result shape.  An operation owns everything specific to a single user-visible
action: how its parameters are validated, what preview it prepares, how it is
executed and how it is restored.

Splitting them this way means a new operation is a new module rather than an
edit to an adapter that is already at its maintenance budget.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from engine.app_actions.base import AppActionBlocked, PreparedAction


@runtime_checkable
class AppOperation(Protocol):
    """One named action for one application.

    ``session`` is the live application handle the adapter has already opened
    and verified.  Operations never open, close or lease an application
    themselves, and never leave COM values in the values they return.
    """

    app: str
    name: str

    def prepare(self, adapter, session, params: dict) -> PreparedAction: ...

    def execute(
        self, adapter, session, prepared: PreparedAction
    ) -> dict[str, Any]: ...


class OperationRegistry:
    """The operations one adapter exposes, resolved by name."""

    def __init__(self, app: str, label: str, operations):
        self.app = str(app)
        self.label = str(label)
        registered: dict[str, Any] = {}
        for operation in operations:
            name = str(getattr(operation, "name", "")).strip()
            if not name:
                raise ValueError("작업 모듈에 name이 없습니다.")
            if getattr(operation, "app", None) != self.app:
                raise ValueError(f"{name} 작업의 app이 {self.app}과 다릅니다.")
            if name in registered:
                raise ValueError(f"중복 등록된 작업입니다: {name}")
            registered[name] = operation
        self._operations = registered
        self.names = frozenset(registered)

    def __contains__(self, name) -> bool:
        return str(name or "").strip() in self._operations

    def get(self, name):
        return self._operations.get(str(name or "").strip())

    def require(self, name):
        operation = self.get(name)
        if operation is None:
            raise AppActionBlocked(
                f"아직 지원하지 않는 {self.label} 작업입니다: {str(name or '').strip()}"
            )
        return operation

    def require_prepared(self, prepared: PreparedAction):
        """Resolve the operation for an already prepared action."""
        if prepared.app != self.app or prepared.operation not in self._operations:
            raise AppActionBlocked(
                f"지원되는 {self.label} PreparedAction만 실행할 수 있습니다."
            )
        return self._operations[prepared.operation]


__all__ = ["AppOperation", "OperationRegistry"]
