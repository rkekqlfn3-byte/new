"""Shared skeleton for PowerPoint operations."""

from __future__ import annotations

from typing import Any

MAX_PPT_TEXT_CHARS = 20_000
PPT_SELECTION_SHAPES = 2
PPT_SELECTION_TEXT = 3
MSO_PLACEHOLDER = 14


class PowerPointSession:
    """The application and presentation the adapter has already opened.

    ``write_started`` is the safety flag behind ``_retry_transient_com``: a
    transient COM failure may be retried only while nothing has been written
    yet, so a partially applied change is never replayed. The operation raises
    the flag immediately before its first write, and the adapter reads it on
    the exception path.
    """

    __slots__ = ("application", "presentation", "write_started")

    def __init__(self, application: Any, presentation: Any):
        self.application = application
        self.presentation = presentation
        self.write_started = False

    def mark_write_started(self) -> None:
        self.write_started = True


class PowerPointTarget:
    """The resolved slide and shape one operation is about to change."""

    __slots__ = ("slide", "shape_id", "shape")

    def __init__(self, slide: Any, shape_id: Any, shape: Any):
        self.slide = slide
        self.shape_id = shape_id
        self.shape = shape


class PowerPointOperation:
    """One PowerPoint action.

    ``execute`` re-prepares from live state, refuses to continue when anything
    moved since approval, short-circuits no-ops, resolves the slide and shape,
    and only then marks the write as started.
    """

    app = "powerpoint"
    name = ""

    def prepare(self, adapter, session: PowerPointSession, params):
        raise NotImplementedError

    def run(self, adapter, target: PowerPointTarget, current):
        raise NotImplementedError

    def execute(self, adapter, session: PowerPointSession, prepared):
        current = self.prepare(adapter, session, prepared.params)
        adapter._ensure_same_context(current, prepared)
        if current.noop:
            return adapter._result(current, False, {"noop": True})
        slide = adapter._slide_by_id(
            session.presentation,
            current.current_state["slide_id"],
            current.current_state["slide_number"],
        )
        shape_id = current.current_state["shape_id"]
        shape = adapter._shape_by_id(slide, shape_id)
        session.mark_write_started()
        return self.run(adapter, PowerPointTarget(slide, shape_id, shape), current)


__all__ = [
    "MAX_PPT_TEXT_CHARS",
    "MSO_PLACEHOLDER",
    "PPT_SELECTION_SHAPES",
    "PPT_SELECTION_TEXT",
    "PowerPointOperation",
    "PowerPointSession",
    "PowerPointTarget",
]
