"""Keep the sentences Jarvis did not understand, so the wording can be fixed.

The incident log already counts failures, but it stores only a hash of what
was typed, so ``같은 표현이 다섯 번 실패했다`` is knowable and ``무슨 말이었나``
is not.  Fixing the vocabulary needs the wording itself: every rule bug found
so far — ``글머리표 없애줘`` deleting a selection, ``왼쪽 셀 지워줘`` filling
left, ``각주 모양 바꾸고 싶어`` inserting a footnote — was found because a
person could say the sentence out loud.

What is kept is deliberately narrow: the sentence, the application, the
reason it was refused, how often, and when.  Nothing about the document —
not its name, not its path, not a word of its contents.

It never leaves this machine.  Nothing here is sent anywhere, and the reader
can read the list and empty it whenever they like.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json

SCHEMA_VERSION = 1

# Enough to see a pattern; a person is not going to read more than this.
MAX_ENTRIES = 300

# A sentence long enough to be a document rather than a request is not a
# wording problem, and keeping it would mean keeping content.
MAX_LENGTH = 300

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", str(text or "")).strip()


class UnrecognisedCommandLog:
    """Sentences that were refused, with how often and how recently."""

    def __init__(self, path=None):
        self._path = str(path or user_data_path("unrecognised_commands.json"))
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
            max_versions=0,
        )

    def record(
        self,
        command: str,
        app_type: str = "",
        reason: str = "",
        when: str = "",
    ) -> bool:
        """Write down one refusal, or say why it was not worth writing."""
        text = normalise(command)
        if not text or len(text) > MAX_LENGTH:
            return False
        with self._lock:
            self._load()
            key = f"{str(app_type or '').casefold()} {text.casefold()}"
            previous = self._entries.get(key) or {}
            self._entries[key] = {
                "command": text,
                "app_type": str(app_type or "").casefold(),
                "reason": normalise(reason)[:200],
                "count": int(previous.get("count", 0)) + 1,
                "first_seen": previous.get("first_seen") or when,
                "last_seen": when,
            }
            if len(self._entries) > MAX_ENTRIES:
                ordered = sorted(
                    self._entries.items(),
                    key=lambda item: (
                        item[1].get("count", 0),
                        item[1].get("last_seen") or "",
                    ),
                )
                for stale, _ in ordered[: len(self._entries) - MAX_ENTRIES]:
                    self._entries.pop(stale, None)
            self._save()
            return True

    def entries(self) -> tuple[dict, ...]:
        """Most repeated first: the wording worth fixing is the frequent one."""
        with self._lock:
            self._load()
            return tuple(
                sorted(
                    (dict(value) for value in self._entries.values()),
                    key=lambda item: (
                        -int(item.get("count", 0)),
                        item.get("last_seen") or "",
                    ),
                )
            )

    def clear(self) -> int:
        with self._lock:
            self._load()
            removed = len(self._entries)
            self._entries = {}
            self._save()
            return removed

    def forget(self, command: str, app_type: str = "") -> bool:
        with self._lock:
            self._load()
            key = f"{str(app_type or '').casefold()} {normalise(command).casefold()}"
            existed = self._entries.pop(key, None) is not None
            if existed:
                self._save()
            return existed


_SHARED: UnrecognisedCommandLog | None = None
_SHARED_LOCK = threading.Lock()


def shared_log() -> UnrecognisedCommandLog:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = UnrecognisedCommandLog()
        return _SHARED


def record_refusal(result: Mapping[str, Any], command: str, app_type: str = "") -> bool:
    """Record a refusal, and only a refusal.

    A cancelled request, a busy application or a request awaiting approval
    is not a wording Jarvis failed to understand.
    """
    if not isinstance(result, Mapping) or result.get("success"):
        return False
    if str(result.get("status") or "").casefold() != "blocked":
        return False
    from datetime import datetime

    return shared_log().record(
        command,
        app_type=app_type,
        reason=str(result.get("message") or "")[:200],
        when=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


__all__ = [
    "MAX_ENTRIES",
    "MAX_LENGTH",
    "UnrecognisedCommandLog",
    "normalise",
    "record_refusal",
    "shared_log",
]
