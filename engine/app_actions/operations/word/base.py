"""Shared skeleton for Word operations."""

from __future__ import annotations

from typing import Any, NamedTuple

MAX_WORD_TEXT_CHARS = 5_000_000
MAX_WORD_SELECTION_CHARS = 50_000
MAX_WORD_REPLACEMENT_CHARS = 10_000
WD_NO_PROTECTION = -1
WD_WITHIN_TABLE = 12


class WordSession(NamedTuple):
    """The application and document the adapter has already opened."""

    application: Any
    document: Any


class WordOperation:
    """One Word action.

    ``execute`` re-prepares from the live document and refuses to continue when
    anything moved since approval, then short-circuits no-op requests, so every
    operation inherits both checks.
    """

    app = "word"
    name = ""

    def prepare(self, adapter, session: WordSession, params):
        raise NotImplementedError

    def run(self, adapter, session: WordSession, current):
        raise NotImplementedError

    def execute(self, adapter, session: WordSession, prepared):
        current = self.prepare(adapter, session, prepared.params)
        adapter._ensure_same_context(current, prepared)
        if current.noop:
            return adapter._result(current, False, {"noop": True})
        return self.run(adapter, session, current)


__all__ = [
    "MAX_WORD_REPLACEMENT_CHARS",
    "MAX_WORD_SELECTION_CHARS",
    "MAX_WORD_TEXT_CHARS",
    "WD_NO_PROTECTION",
    "WD_WITHIN_TABLE",
    "WordOperation",
    "WordSession",
]
