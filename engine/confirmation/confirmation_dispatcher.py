from engine.confirmation.context import ConfirmationContext
from engine.confirmation.handlers import HANDLERS
from engine.execution_result import failure_result


class ConfirmationDispatcher:
    """Resume consumed confirmation records through domain-specific handlers."""

    def __init__(self, response_handler, handlers=None):
        self.response_handler = response_handler
        self.handlers = dict(handlers or HANDLERS)

    def resolve(
        self,
        runtime,
        session_id,
        confirmation_id=None,
        option_id=None,
        user_text=None,
        log_callback=None,
        remember_preference=False,
    ):
        resolution = self.response_handler.resolve_selection(
            session_id,
            confirmation_id=confirmation_id,
            option_id=option_id,
            user_text=user_text,
            remember_preference=remember_preference,
            cancel_pending_edit=getattr(
                runtime.edit_mode_controller, "cancel_pending_edit", None
            ),
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
        handler = self.handlers.get(context.payload.get("kind"))
        if handler is None:
            return failure_result(
                "지원하지 않는 확인 후속 작업입니다.",
                action="confirmation", error_type="validation_error",
            )
        return handler(runtime, context)
