"""Shared skeleton for Excel operations.

Unlike the other adapters, Excel's executors are not uniform: ``write_cell``
re-reads the target cell and compares the fingerprint itself, while the table
operations re-prepare and call ``_ensure_same_context``.  Forcing a shared
preamble would change behaviour, so each operation owns its ``execute`` and
this base only declares the interface and the undo opt-in.
"""

from __future__ import annotations

MAX_SOURCE_CELLS = 10000
MAX_DIRECT_FORMAT_CELLS = 1000
MAX_RANGE_FORMAT_CELLS = 5000
MAX_FIND_REPLACE_CELLS = 10000
MAX_SORT_ROWS = 5000
MAX_TABLE_CELLS = 20000


class ExcelOperation:
    """One Excel action.

    ``application`` is the live Excel Application the adapter has already
    opened and verified. Operations never acquire or release it.

    An operation that can be restored implements ``undo``; the adapter derives
    ``undo_supported_operations`` from which operations do, so a new reversible
    action brings its own restore path instead of needing an adapter edit.
    """

    app = "excel"
    name = ""

    def prepare(self, adapter, application, params):
        raise NotImplementedError

    def execute(self, adapter, application, prepared):
        raise NotImplementedError

    # Optional. Present only on reversible operations.
    undo = None


__all__ = [
    "MAX_DIRECT_FORMAT_CELLS",
    "MAX_FIND_REPLACE_CELLS",
    "MAX_RANGE_FORMAT_CELLS",
    "MAX_SORT_ROWS",
    "MAX_SOURCE_CELLS",
    "MAX_TABLE_CELLS",
    "ExcelOperation",
]
