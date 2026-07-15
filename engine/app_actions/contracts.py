"""Type contracts for current adapters and future Office extensions."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from engine.app_actions.base import PreparedAction


@runtime_checkable
class NativeAppAdapter(Protocol):
    """Runtime surface currently required by ``AppActionRegistry``."""

    supported_operations: frozenset[str]

    def prepare(self, operation: str, params: dict) -> PreparedAction: ...

    def execute(self, prepared: PreparedAction) -> dict[str, Any]: ...


class OfficeExtensionContract(Protocol):
    """Target boundary for adding Word and PowerPoint adapters.

    Excel and HWP keep their existing facades during the staged migration. New
    adapters should keep COM values inside these methods and exchange only
    serializable request, prepared-action, result, and context values.
    """

    def prepare(self, request: Mapping[str, Any]) -> PreparedAction: ...

    def execute(self, prepared_action: PreparedAction) -> dict[str, Any]: ...

    def verify(
        self, prepared_action: PreparedAction, result: Mapping[str, Any]
    ) -> bool: ...

    def rollback(self, prepared_action: PreparedAction) -> bool: ...

    def fingerprint(self, context: Mapping[str, Any]) -> str: ...

