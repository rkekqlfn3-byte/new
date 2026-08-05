"""Remember wordings the provider worked out, so the rules answer next time.

Layer 2 asks a provider whenever the rules do not recognise a sentence, and
it asks again for the same sentence every time.  Saying ``행간 1.5로`` a
hundred times costs a hundred requests, a hundred waits and a hundred
chances for an offline machine to refuse work it has already done once.

So a translation that was approved and executed successfully is written
down, and the next request for that wording is answered from the note
without a request.  This is the fourth layer of the contract in
``JARVIS_ARCHITECTURE_CONTRACT.md``.

Two limits are deliberate:

*Only success is remembered.*  A translation the reader cancelled, or one
that failed verification, is not evidence that the wording means what the
provider said.  Remembering it would make a single wrong guess permanent.

*Only the exact wording is recalled.*  ``행간 1.5로`` is remembered;
``행간 2로`` is a different note and asks again.  Matching loosely would
save requests by applying the first sentence's numbers to the second, which
is the one mistake that turns a saved request into a wrong edit.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json

SCHEMA_VERSION = 1

# Enough for years of one person's editing; the cap exists so a runaway
# caller cannot grow the file without bound.
MAX_ENTRIES = 2000

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """The form two requests must share to count as the same wording."""
    return _WHITESPACE.sub(" ", str(text or "")).strip().casefold()


def _key(app_type: str, text: str) -> str:
    return f"{str(app_type or '').casefold()}|{normalise(text)}"


class EditIntentMemory:
    """Wordings this machine has already had translated, and what they meant."""

    def __init__(self, path=None):
        self._path = str(path or user_data_path("edit_intent_memory.json"))
        self._lock = threading.RLock()
        self._entries: dict[str, dict] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        data = safe_read_json(self._path, {}) or {}
        entries = data.get("entries") if isinstance(data, Mapping) else None
        self._entries = dict(entries) if isinstance(entries, Mapping) else {}
        self._loaded = True

    def _save(self) -> None:
        atomic_write_json(
            self._path,
            {"schema_version": SCHEMA_VERSION, "entries": self._entries},
            max_versions=3,
        )

    def recall(self, app_type: str, text: str):
        """What this wording meant last time, or nothing."""
        if not str(text or "").strip():
            return None
        with self._lock:
            self._load()
            entry = self._entries.get(_key(app_type, text))
            if not isinstance(entry, Mapping):
                return None
            operation = str(entry.get("operation") or "")
            if not operation:
                return None
            params = entry.get("params")
            return operation, dict(params) if isinstance(params, Mapping) else {}

    def remember(
        self, app_type: str, text: str, operation: str, params: Mapping[str, Any]
    ) -> bool:
        """Write down a translation that was approved and actually worked."""
        if not str(text or "").strip() or not str(operation or "").strip():
            return False
        with self._lock:
            self._load()
            key = _key(app_type, text)
            previous = self._entries.get(key) or {}
            self._entries[key] = {
                "app_type": str(app_type or "").casefold(),
                "command": normalise(text),
                "operation": str(operation),
                "params": dict(params or {}),
                "uses": int(previous.get("uses", 0)) + 1,
            }
            if len(self._entries) > MAX_ENTRIES:
                # Drop the least used rather than the oldest: a wording used
                # once a year is worth more than one used once ever.
                ordered = sorted(
                    self._entries.items(), key=lambda item: item[1].get("uses", 0)
                )
                for stale, _ in ordered[: len(self._entries) - MAX_ENTRIES]:
                    self._entries.pop(stale, None)
            self._save()
            return True

    def forget(self, app_type: str | None = None) -> int:
        with self._lock:
            self._load()
            if app_type is None:
                removed = len(self._entries)
                self._entries = {}
            else:
                wanted = str(app_type).casefold()
                keep = {
                    key: value
                    for key, value in self._entries.items()
                    if value.get("app_type") != wanted
                }
                removed = len(self._entries) - len(keep)
                self._entries = keep
            self._save()
            return removed

    def entries(self) -> tuple[dict, ...]:
        with self._lock:
            self._load()
            return tuple(dict(value) for value in self._entries.values())


_SHARED: EditIntentMemory | None = None
_SHARED_LOCK = threading.Lock()


def shared_memory() -> EditIntentMemory:
    """One store per process; the file is what actually persists."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = EditIntentMemory()
        return _SHARED


__all__ = [
    "MAX_ENTRIES",
    "EditIntentMemory",
    "normalise",
    "shared_memory",
]
