import copy

from engine.action_executor import ActionPlanError
from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.ui_automation import UIAutomationAmbiguousTarget


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    option_id = context.option_id
    consumed = context.consumed
    log_callback = context.log_callback
    execution_id = context.execution_id
    payload = context.payload
    plan = copy.deepcopy(payload.get("plan", []))
    selectors = payload.get("candidate_selectors", {})
    selector = copy.deepcopy(selectors.get(option_id))
    try:
        failed_step = int(payload.get("failed_step") or 1)
    except (TypeError, ValueError):
        failed_step = 1
    if (
        not isinstance(selector, dict)
        or failed_step < 1
        or failed_step > len(plan)
    ):
        return failure_result(
            "선택한 UI 요소의 실행 정보를 복원할 수 없습니다.",
            action="action_plan",
            error_type="validation_error",
            status="context_changed",
        )
    plan[failed_step - 1]["selector"] = selector
    try:
        if payload.get("resume_mode") == "learned_skill":
            learned_skill = copy.deepcopy(
                payload.get("learned_skill", {})
            )
            plan_key = payload.get("plan_key")
            if plan_key not in {"plan", "native_plan", "uia_plan"}:
                raise ActionPlanError(
                    "학습 UIA 경로 정보가 올바르지 않습니다."
                )
            learned_skill[plan_key] = plan
            selected_route = {
                "plan": "action_plan",
                "native_plan": "native",
                "uia_plan": "uia",
            }[plan_key]
            profile = dict(learned_skill.get("execution_profile") or {})
            profile.update({
                "primary_route": selected_route,
                "fallback_routes": [],
                "max_fallback_attempts": 0,
            })
            learned_skill["execution_profile"] = profile
            execution_result = self.skill_executor.execute(
                payload.get("app_name"),
                payload.get("macro_name"),
                skill=learned_skill,
                argument=payload.get("argument", ""),
                slots=payload.get("slots", {}),
                source=payload.get("source", "confirmation_resume"),
                action="use_learned_macro",
                target=payload.get("target", ""),
                log_callback=log_callback,
                start_step=failed_step,
                session_id=session_id,
                original_command=consumed.get("original_command", ""),
            )
        else:
            execution_result = self.action_executor.execute_plan(
                plan,
                payload.get("slots", {}),
                log_callback,
                start_step=failed_step,
            )
    except UIAutomationAmbiguousTarget as error:
        next_payload = copy.deepcopy(payload)
        next_payload["plan"] = plan
        if payload.get("resume_mode") == "learned_skill":
            next_payload["learned_skill"] = learned_skill
        next_payload["failed_step"] = getattr(
            error, "failed_step", failed_step
        )
        next_payload["execution_id"] = execution_id
        return self._queue_uia_target_choice(
            error,
            next_payload,
            session_id,
            consumed.get("original_command", ""),
        )
    except ExecutionCancelled:
        raise
    except Exception as error:
        return failure_result(
            f"선택한 UI 요소 작업을 실행하지 못했습니다: {error}",
            action="action_plan",
            error_type=self._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", failed_step),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
        )

    if execution_result.get("status") == "confirmation_required":
        return execution_result

    candidate = payload.get("learning_candidate")
    if isinstance(candidate, dict):
        candidate = copy.deepcopy(candidate)
        candidate["plan"] = plan
        candidate["verification_status"] = execution_result.get(
            "verification_status", "confirmation_required"
        )
        candidate["verification"] = execution_result.get(
            "verification", []
        )
        self.skill_learning_service.stage_candidate(candidate)
    learned_resume = payload.get("resume_mode") == "learned_skill"
    return success_result(
        (
            "선택한 UI 요소로 학습 행동을 실행했습니다."
            if learned_resume
            else "선택한 UI 요소로 작업을 실행했습니다."
        ),
        action="learned_macro" if learned_resume else "action_plan",
        target=(
            payload.get("macro_name") if learned_resume else None
        ),
        verified=bool(execution_result.get("verified", False)),
        verification_status=execution_result.get(
            "verification_status", "confirmation_required"
        ),
        data={
            "confirmation_id": consumed["confirmation_id"],
            "selected_selector": selector,
            "execution_result": execution_result,
        },
    )
