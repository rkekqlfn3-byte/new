from engine.action_executor import ActionPlanError
from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.skills import (
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillPreflightBlocked,
    skill_policy_fingerprint,
)
from engine.ui_automation import UIAutomationAmbiguousTarget


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    option_id = context.option_id
    consumed = context.consumed
    log_callback = context.log_callback
    payload = context.payload
    app_name = payload.get("app_name")
    macro_name = payload.get("macro_name")
    learned = self._get_learned_macro(app_name, macro_name)
    if not isinstance(learned, dict):
        return failure_result(
            "확인 후 학습 스킬을 다시 찾지 못했습니다.",
            action="learned_macro",
            target=macro_name,
            error_type="target_not_found",
        )
    if skill_policy_fingerprint(learned) != payload.get(
        "skill_fingerprint"
    ):
        return failure_result(
            "확인하는 동안 스킬 코드나 실행 계획이 바뀌어 실행하지 않았습니다.",
            action="learned_macro",
            target=macro_name,
            error_type="validation_error",
            status="context_changed",
        )
    if option_id == "enable_auto":
        try:
            self.dict_mgr.set_learned_macro_run_policy(
                app_name,
                macro_name,
                "auto",
                changed_by="user",
                reason=(
                    "연속 자동 검증 성공 후 확인 카드에서 사용자가 자동 실행을 승인"
                ),
            )
        except ValueError as error:
            return failure_result(
                str(error),
                action="learned_macro",
                target=macro_name,
                error_type="validation_error",
                status="context_changed",
            )
        learned = self._get_learned_macro(app_name, macro_name)

    if payload.get("resume_mode") == "ai_batch":
        approved = set(payload.get("approved_skill_runs", []))
        approved.add(f"{app_name}/{macro_name}".casefold())
        return self._execute_ai_action_batch(
            payload.get("response", ""),
            payload.get("actions", []),
            payload.get("validation_issues", []),
            payload.get("user_input", consumed.get("original_command", "")),
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=payload.get(
                "approval_fingerprints", []
            ),
            approved_skill_runs=sorted(approved),
            confirmation_id=consumed["confirmation_id"],
            image_data=payload.get("image_data"),
            use_api=bool(payload.get("use_api", False)),
        )

    try:
        execution_result = self.skill_executor.execute(
            app_name,
            macro_name,
            skill=learned,
            argument=payload.get("argument", ""),
            slots=payload.get("slots", {}),
            source=payload.get("source", "local_match_policy_resume"),
            action="use_learned_macro",
            target=payload.get("target", ""),
            log_callback=log_callback,
            session_id=session_id,
            original_command=consumed.get("original_command", ""),
        )
    except SkillPreflightBlocked as error:
        return self._dynamic_preflight_failure(
            error.preflight,
            action="learned_macro",
            target=macro_name,
        )
    except SkillConfirmationRequired as error:
        return self.confirmations.queue_local_learned_dynamic(
            self,
            app_name,
            macro_name,
            payload.get("argument", ""),
            error.preflight,
            error.fingerprint,
            session_id,
            consumed.get("original_command", ""),
        )
    except UIAutomationAmbiguousTarget as error:
        return self.confirmations.queue_learned_uia_target(
            self,
            error,
            app_name=app_name,
            macro_name=macro_name,
            skill=learned,
            slots=payload.get("slots", {}),
            session_id=session_id,
            original_command=consumed.get("original_command", ""),
            argument=payload.get("argument", ""),
            source="local_match_policy_uia_resume",
            target=payload.get("target", ""),
        )
    except (SkillContextChanged, ActionPlanError) as error:
        return failure_result(
            str(error),
            action="learned_macro",
            target=macro_name,
            error_type=self._failure_type_for_error(error),
            status=getattr(error, "status", "context_changed"),
        )
    except ExecutionCancelled:
        raise
    except Exception as error:
        return failure_result(
            f"확인한 학습 스킬을 실행하지 못했습니다: {error}",
            action="learned_macro",
            target=macro_name,
            error_type=self._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
        )
    if execution_result.get("status") == "confirmation_required":
        return execution_result
    verified = bool(execution_result.get("verified", False))
    return success_result(
        f"확인한 학습 행동 [{macro_name}]을 실행했습니다.",
        action="learned_macro",
        target=macro_name,
        verified=verified,
        verification_status=execution_result.get(
            "verification_status",
            "verified" if verified else "confirmation_required",
        ),
        data={
            "app": app_name,
            "confirmation_id": consumed["confirmation_id"],
            "execution_result": execution_result,
            "run_policy": learned.get("run_policy", "confirm"),
        },
    )
