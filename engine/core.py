"""Process-local service container with explicit, thread-safe initialization."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from engine.managers.dict_manager import DictionaryManager
    from engine.parser import CommandParser


@dataclass(frozen=True)
class CoreServices:
    parser: "CommandParser"

    @property
    def dict_mgr(self) -> "DictionaryManager":
        return self.parser.dict_mgr


_lock = threading.RLock()
_services: CoreServices | None = None


def initialize_core(
    parser_factory: Callable[[], "CommandParser"] | None = None,
) -> CoreServices:
    """Create the application services once, after user data is initialized."""
    global _services
    with _lock:
        if _services is not None:
            return _services
        if parser_factory is None:
            # Keep the heavy parser import behind the initialization boundary.
            from engine.parser import CommandParser

            parser_factory = CommandParser
        parser = parser_factory()
        _services = CoreServices(parser=parser)
        return _services


def get_core() -> CoreServices:
    """Return the process singleton, lazily initializing non-app entrypoints."""
    return initialize_core()


def get_parser() -> "CommandParser":
    return get_core().parser


def get_dict_manager() -> "DictionaryManager":
    return get_core().dict_mgr
