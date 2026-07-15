from engine.execution_result import failure_result, success_result


def finalize_batch_result(context):
    if context.failures:
        first = context.failures[0]
        return failure_result(
            context.response,
            action=first.get("action", "ai_command"),
            target=first.get("target"),
            error_type=first.get("error_type", "execution_error"),
            failed_step=first.get("failed_step"),
            retryable=first.get("retryable", False),
            data={"failures": context.failures},
            status=first.get("status", "failed"),
        )

    verified = bool(context.action_results) and all(
        item.get("success", False) and item.get("verified", False)
        for item in context.action_results
    )
    result_data = {
        "action_count": len(context.actions),
        "action_results": context.action_results,
    }
    if context.confirmation_id:
        result_data["confirmation_id"] = context.confirmation_id
    return success_result(
        context.response,
        action="ai_command",
        verified=verified,
        verification_status=(
            "verified" if verified else "confirmation_required"
        ),
        data=result_data,
    )
