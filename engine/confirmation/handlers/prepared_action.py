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
    continuation_error = None
    if isinstance(continuation, dict) and continuation.get("kind") == "learned_native":
        _, continuation_error = self.skill_executor.validate_native_continuation(
            continuation
        )
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
        if self.confirmations.responses.context_changed(
            previous, current
        ):
            decision = self.decision_engine.evaluate(
                current, force_confirmation=True
            )
            return self.confirmations.queue_prepared_action(
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
        if isinstance(continuation, dict) and continuation.get("kind") == "learned_native":
            return self.skill_executor.complete_native_confirmation(continuation, result)
        return result
    except AppActionContextChanged:
        return self.app_command_router.queue_changed_context(
            request,
            previous,
            session_id,
            consumed.get("original_command", ""),
            execution_id=execution_id,
            preference_selection=preference_selection,
            continuation=continuation,
            runtime=self,
        )
    except AppActionError as error:
        result = self.app_command_router.failure(
            error,
            target=(
                f"{previous.workbook_name}/{previous.sheet}/{previous.target}"
                if isinstance(previous, PreparedAction) else None
            ),
        )
        if isinstance(continuation, dict) and continuation.get("kind") == "learned_native":
            return self.skill_executor.complete_native_confirmation(continuation, result)
        return result
