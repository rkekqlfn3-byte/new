import copy

from engine.action_executor import ActionConfirmationRequired
from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    consumed = context.consumed
    log_callback = context.log_callback
    execution_id = context.execution_id
    payload = context.payload
    try:
        authorized_plan = self._authorize_plan_overwrite(
            payload.get("plan", []),
            payload.get("confirmation_action"),
            payload.get("confirmation_target"),
        )
        execution_result = self.action_executor.execute_plan(
            authorized_plan,
            payload.get("slots", {}),
            log_callback,
        )
    except ActionConfirmationRequired as error:
        payload["plan"] = authorized_plan
        payload["execution_id"] = execution_id
        return self._queue_action_plan_confirmation(
            error, payload, session_id, consumed.get("original_command", "")
        )
    except ExecutionCancelled:
        raise
    except Exception as error:
        return failure_result(
            f"확인한 작업을 실행하지 못했습니다: {error}",
            action="action_plan",
            error_type=self._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
        )

    candidate = payload.get("learning_candidate")
    if isinstance(candidate, dict):
        candidate = copy.deepcopy(candidate)
        candidate["plan"] = authorized_plan
        candidate["verification_status"] = execution_result.get(
            "verification_status", "confirmation_required"
        )
        candidate["verification"] = execution_result.get("verification", [])
        self.skill_learning_service.stage_candidate(candidate)
    return success_result(
        "확인한 방식으로 작업을 실행했습니다.",
        action="action_plan",
        verified=bool(execution_result.get("verified", False)),
        verification_status=execution_result.get(
            "verification_status", "confirmation_required"
        ),
        data={
            "confirmation_id": consumed["confirmation_id"],
            "execution_result": execution_result,
        },
    )
