"""Generic confirmation response parsing and one-shot consumption.

This module deliberately does not know how Excel, HWP, action plans, or dynamic
Python work. It owns the common confirmation boundary; CommandParser resumes the
domain-specific payload only after this handler has validated and consumed it.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from engine.execution_result import confirmation_result, failure_result
from engine.managers.pending_confirmation_manager import (
    PendingConfirmationError,
    normalize_session_id,
)


@dataclass(frozen=True)
class ConfirmationResolution:
    """Result of resolving one confirmation response."""

    session_id: str
    option_id: Optional[str] = None
    consumed: Optional[Dict[str, Any]] = None
    remember_preference: bool = False
    result: Optional[Dict[str, Any]] = None


class ConfirmationResponseHandler:
    """Validate, interpret, and consume confirmation responses exactly once."""

    _REMEMBER_WORDS = ("앞으로", "항상", "무조건", "기억해")

    def __init__(self, owner):
        self.owner = owner

    @property
    def manager(self):
        return self.owner.pending_confirmation_manager

    @property
    def execution_controller(self):
        return self.owner.execution_controller

    def get_pending(self, session_id=None):
        confirmation = self.manager.active_record(
            normalize_session_id(session_id), public=True
        )
        self._drop_expired_executions()
        return confirmation

    def confirmation_result(self, record, message=None):
        public = self.manager.public_record(record)
        return confirmation_result(
            message or public.get("message", "확인이 필요합니다."),
            public,
            action=public.get("action", "confirmation"),
            target=public.get("target"),
        )

    def resolve_selection(
        self,
        session_id,
        confirmation_id=None,
        option_id=None,
        user_text=None,
        remember_preference=False,
    ):
        """Resolve text/button input and consume its record before any action."""
        session_id = normalize_session_id(session_id)
        record = (
            self.manager.get_record(confirmation_id)
            if confirmation_id
            else self.manager.active_record(session_id)
        )
        self._drop_expired_executions()
        if not record:
            return ConfirmationResolution(
                session_id=session_id,
                result=failure_result(
                    "처리할 확인 요청을 찾을 수 없습니다.",
                    action="confirmation",
                    error_type="validation_error",
                    status="confirmation_not_found",
                ),
            )

        if record.get("rememberable") and user_text:
            normalized_answer = str(user_text).strip().casefold()
            remember_preference = bool(remember_preference) or any(
                word in normalized_answer for word in self._REMEMBER_WORDS
            )
        remember_preference = bool(
            remember_preference and record.get("rememberable")
        )

        if option_id is None:
            option_id = self.manager.resolve_text(session_id, user_text)
            if option_id is None:
                return ConfirmationResolution(
                    session_id=session_id,
                    remember_preference=remember_preference,
                    result=self.confirmation_result(
                        record,
                        "확인 요청이 대기 중입니다. 아래 선택지 중 하나를 선택하거나 취소해주세요.",
                    ),
                )

        execution_id = record.get("execution_id", "")
        # A confirmation response belongs to the paused execution that
        # created it.  Verify that this exact execution can regain the single
        # runtime slot *before* consuming the one-shot confirmation record.
        # ``begin`` also blocks while pending, so a successful check remains
        # valid through the immediate resume below.
        if (
            execution_id
            and record.get("status") == "pending"
            and not self.execution_controller.can_resume(execution_id)
        ):
            self.execution_controller.pending_event(
                execution_id,
                "confirmation",
                "resume_blocked",
                {"confirmation_id": record["confirmation_id"]},
            )
            return ConfirmationResolution(
                session_id=session_id,
                option_id=option_id,
                remember_preference=remember_preference,
                result=failure_result(
                    "The confirmation response could not safely resume its command.",
                    action="confirmation",
                    error_type="busy",
                    status="state_conflict",
                    retryable=True,
                ),
            )

        try:
            consumed = self.manager.consume(
                session_id, record["confirmation_id"], option_id
            )
        except PendingConfirmationError as error:
            return ConfirmationResolution(
                session_id=session_id,
                option_id=option_id,
                remember_preference=remember_preference,
                result=failure_result(
                    str(error),
                    action="confirmation",
                    error_type="validation_error",
                    status=getattr(error, "status", "confirmation_error"),
                ),
            )

        execution_id = consumed.get("execution_id", "")
        if execution_id and not self.execution_controller.resume(execution_id):
            # This branch should be unreachable because a pending execution
            # reserves the only command slot.  Keep it explicit so an
            # unexpected state transition is observable rather than allowing
            # a confirmation to run against the wrong execution.
            return ConfirmationResolution(
                session_id=session_id,
                option_id=option_id,
                consumed=consumed,
                remember_preference=remember_preference,
                result=failure_result(
                    "The confirmation response was not applied because execution state changed.",
                    action="confirmation",
                    error_type="busy",
                    status="state_conflict",
                    retryable=True,
                ),
            )

        selected = next(
            item for item in consumed["options"] if item["id"] == option_id
        )
        if selected.get("cancel"):
            return ConfirmationResolution(
                session_id=session_id,
                option_id=option_id,
                consumed=consumed,
                remember_preference=remember_preference,
                result=failure_result(
                    "확인 요청을 취소했습니다. 외부 변경은 실행하지 않았습니다.",
                    action=consumed.get("action", "confirmation"),
                    target=consumed.get("target"),
                    error_type="user_cancelled",
                    data={"confirmation_id": consumed["confirmation_id"]},
                ),
            )

        return ConfirmationResolution(
            session_id=session_id,
            option_id=option_id,
            consumed=consumed,
            remember_preference=remember_preference,
        )

    @staticmethod
    def context_changed(previous, current):
        """Recheck a prepared document/app fingerprint immediately before resume."""
        return current.context_fingerprint != previous.context_fingerprint

    def _drop_expired_executions(self):
        for execution_id in self.manager.drain_expired_execution_ids():
            self.execution_controller.drop_pending(execution_id)
