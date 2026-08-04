"""Shared skeleton for Hanword operations."""

from __future__ import annotations

MAX_DOCUMENT_TEXT_CHARS = 5_000_000
MAX_INSERT_TEXT_CHARS = 5_000
MAX_REPLACE_MATCHES = 10_000
MAX_FORMAT_SELECTION_CHARS = 100_000


class HwpOperation:
    """One Hanword action.

    Subclasses implement ``prepare`` (build the preview from live state) and
    ``run`` (apply and verify it).  ``execute`` re-prepares from the current
    document and refuses to continue when anything moved since approval, so
    every operation gets that check without repeating it.
    """

    app = "hwp"
    name = ""

    def prepare(self, adapter, hwp, params):
        raise NotImplementedError

    def run(self, adapter, hwp, current):
        raise NotImplementedError

    def execute(self, adapter, hwp, prepared):
        current = self.prepare(adapter, hwp, prepared.params)
        adapter._ensure_same_context(current, prepared)
        return self.run(adapter, hwp, current)


__all__ = [
    "MAX_DOCUMENT_TEXT_CHARS",
    "MAX_FORMAT_SELECTION_CHARS",
    "MAX_INSERT_TEXT_CHARS",
    "MAX_REPLACE_MATCHES",
    "HwpOperation",
]
