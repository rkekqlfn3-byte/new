from engine.app_actions import AppActionContextChanged, AppActionError, PreparedAction


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    option_id = context.option_id
    consumed = context.consumed
    remember_preference = context.remember_preference
    execution_id = context.execution_id
    payload = context.payload
    requests = payload.get("requests", {})
    prepared_actions = payload.get("prepared_actions", {})
    request = requests.get(option_id, {})
    previous_data = prepared_actions.get(option_id, {})
    preference_selection = {
        "key": payload.get("preference_key"),
        "method": option_id,
        "remember": remember_preference,
    }
    previous = None
    try:
        previous = PreparedAction.from_dict(previous_data)
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
            )
        return self.app_command_router.execute_prepared(
            current,
            confirmation_id=consumed["confirmation_id"],
            preference_selection=preference_selection,
        )
    except AppActionContextChanged:
        return self._queue_changed_app_context(
            request,
            previous,
            session_id,
            consumed.get("original_command", ""),
            execution_id=execution_id,
            preference_selection=preference_selection,
        )
    except AppActionError as error:
        return self._app_action_failure(
            error,
            target=(
                f"{previous.workbook_name}/{previous.sheet}/{previous.target}"
                if isinstance(previous, PreparedAction) else None
            ),
        )
