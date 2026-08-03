"""Native application actions prepared from live application state."""

from engine.app_actions.app_command_router import AppCommandRouter
from engine.app_actions.base import (
    AppActionAmbiguousTarget,
    AppActionBlocked,
    AppActionBusy,
    AppActionContextChanged,
    AppActionError,
    AppActionUnavailable,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.contracts import (
    NativeAppAdapter,
    OfficeExtensionContract,
    StructuredEditAdapter,
)
from engine.app_actions.excel_vba_adapter import (
    ExcelVbaAdapter,
    VbaTrustAccessBlocked,
    analyze_vba_code,
    parse_vba_procedures,
    vba_trust_status,
)
from engine.app_actions.powerpoint_adapter import PowerPointAdapter
from engine.app_actions.registry import AppActionRegistry
from engine.app_actions.word_adapter import WordAdapter

__all__ = [
    "AppActionAmbiguousTarget",
    "AppActionBlocked",
    "AppActionBusy",
    "AppActionContextChanged",
    "AppCommandRouter",
    "AppActionError",
    "AppActionRegistry",
    "AppActionUnavailable",
    "AppActionVerificationError",
    "ExcelVbaAdapter",
    "VbaTrustAccessBlocked",
    "NativeAppAdapter",
    "OfficeExtensionContract",
    "StructuredEditAdapter",
    "PreparedAction",
    "PowerPointAdapter",
    "WordAdapter",
    "analyze_vba_code",
    "parse_vba_procedures",
    "vba_trust_status",
]
