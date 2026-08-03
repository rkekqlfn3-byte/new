from engine.ai_actions.batch_action_dispatcher import dispatch_batch_action
from engine.ai_actions.batch_context import BatchExecutionContext
from engine.ai_actions.batch_result_aggregator import finalize_batch_result


class BatchExecutor:
    def execute(
        self,
        parser,
        response,
        actions,
        validation_issues,
        user_input_str,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        context = BatchExecutionContext.create(
            parser,
            response,
            actions,
            validation_issues,
            user_input_str,
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=approved_fingerprints,
            confirmation_id=confirmation_id,
            image_data=image_data,
            use_api=use_api,
        )
        for index, action in enumerate(actions):
            action_name = action.get("action", "none")
            context.log(
                f"[LLM] Action {index + 1}/{len(actions)}: "
                f"{action_name}, Target: {action.get('target')}"
            )
            immediate_result = dispatch_batch_action(context, action)
            if immediate_result is not None:
                return immediate_result
            if action_name == "open_app" and index < len(actions) - 1:
                parser.execution_controller.wait(0.15)
        return finalize_batch_result(context)
