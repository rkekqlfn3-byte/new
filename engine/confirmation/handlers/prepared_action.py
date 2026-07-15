from engine.app_actions import AppActionContextChanged, AppActionError, PreparedAction


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    consumed = context.consumed
    execution_id = context.execution_id
    payload = context.payload
    request = payload.get("request", {})
    preference_selection = payload.get("preference_selection")
    continuation = payload.get("continuation")
    continuation_error = self._validate_app_command_continuation(continuation)
    if continuation_error is not None:
        return continuation_error
    previous = None
    try:
        previous = PreparedAction.from_dict(
            payload.get("prepared_action", {})
        )
        current = self.app_command_router.prepare(
            request.get("target"),
            request.get("operation"),
            request.get("params", {}),
        )
        if self.confirmation_response_handler.context_changed(
            previous, current
        ):
            decision = self.decision_engine.evaluate(
                current, force_confirmation=True
            )
            return self._queue_prepared_action_confirmation(
                current,
                request,
                decision,
                session_id,
                consumed.get("original_command", ""),
                execution_id=execution_id,
                preference_selection=preference_selection,
                continuation=continuation,
            )
        result = self.app_command_router.execute_prepared(
            current,
            confirmation_id=consumed["confirmation_id"],
            preference_selection=preference_selection,
        )
        return self._complete_app_command_continuation(result, continuation)
    except AppActionContextChanged:
        return self._queue_changed_app_context(
            request,
            previous,
            session_id,
            consumed.get("original_command", ""),
            execution_id=execution_id,
            preference_selection=preference_selection,
            continuation=continuation,
        )
    except AppActionError as error:
        result = self._app_action_failure(
            error,
            target=(
                f"{previous.workbook_name}/{previous.sheet}/{previous.target}"
                if isinstance(previous, PreparedAction) else None
            ),
        )
        return self._complete_app_command_continuation(result, continuation)
