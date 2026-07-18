from engine.confirmation.context import ConfirmationContext
from engine.confirmation.handlers import HANDLERS
from engine.execution_result import failure_result


class ConfirmationDispatcher:
    """Resume consumed confirmation records through domain-specific handlers."""

    def __init__(self, owner):
        self.owner = owner

    def resolve(
        self,
        session_id,
        confirmation_id=None,
        option_id=None,
        user_text=None,
        log_callback=None,
        remember_preference=False,
    ):
        resolution = self.owner.confirmation_response_handler.resolve_selection(
            session_id,
            confirmation_id=confirmation_id,
            option_id=option_id,
            user_text=user_text,
            remember_preference=remember_preference,
        )
        if resolution.result is not None:
            return resolution.result

        context = ConfirmationContext(
            session_id=resolution.session_id,
            option_id=resolution.option_id,
            consumed=resolution.consumed,
            remember_preference=resolution.remember_preference,
            feedback_text=resolution.feedback_text,
            log_callback=log_callback,
        )
        handler = HANDLERS.get(context.payload.get("kind"))
        if handler is None:
            return failure_result(
                "지원하지 않는 확인 후속 작업입니다.",
                action="confirmation", error_type="validation_error",
            )
        return handler(self.owner, context)
