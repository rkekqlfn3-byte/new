from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ConfirmationContext:
    session_id: str
    option_id: Optional[str]
    consumed: Dict[str, Any]
    remember_preference: bool = False
    log_callback: Any = None

    @property
    def payload(self):
        return self.consumed.get("payload", {})

    @property
    def execution_id(self):
        return self.consumed.get("execution_id", "")
