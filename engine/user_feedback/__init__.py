"""Privacy-safe user-facing execution feedback."""

from engine.user_feedback.event_adapter import (
    event_from_execution_result,
    event_from_runtime_event,
)
from engine.user_feedback.event_models import UserFeedbackEvent
from engine.user_feedback.natural_language_renderer import render_user_event

__all__ = [
    "UserFeedbackEvent",
    "event_from_execution_result",
    "event_from_runtime_event",
    "render_user_event",
]
