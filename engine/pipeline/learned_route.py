from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.skills import SkillConfirmationRequired, SkillPreflightBlocked
from engine.ui_automation import UIAutomationAmbiguousTarget


def execute_learned_route(
    parser,
    *,
    matched_macro,
    macro_data,
    template_match,
    matched_app_name,
    matched_app_path,
    raw_user_input,
    session_id=None,
    log_callback=None,
):
    app_name = macro_data.get("app")
    learned_macro = getattr(parser.dict_mgr, "learned_macros", {}).get(
        app_name, {}
    ).get(matched_macro)
    if not isinstance(learned_macro, dict):
        return failure_result(
            "사전 매칭된 학습 행동을 찾지 못했습니다.",
            action="learned_macro",
            target=matched_macro,
            error_type="target_not_found",
            data={"app": app_name},
        )
    if learned_macro.get("state", "active") != "active":
        return failure_result(
            "이 학습 행동은 검토 또는 비활성 상태입니다. 행동 사전에서 상태를 확인해주세요.",
            action="learned_macro",
            target=matched_macro,
            error_type="validation_error",
            data={"app": app_name},
        )

    slots = parser._build_slot_values(
        learned_macro.get("learning", {}),
        template_match,
        matched_app_name,
        matched_app_path,
    )
    target_arg = parser._build_learned_argument(
        learned_macro,
        template_match,
        matched_app_name,
        matched_app_path,
    )
    policy_gate = parser._local_skill_policy_gate(
        app_name=app_name,
        macro_name=matched_macro,
        skill=learned_macro,
        slots=slots,
        argument=target_arg,
        session_id=session_id,
        original_command=raw_user_input,
    )
    if policy_gate is not None:
        return policy_gate

    try:
        if log_callback:
            log_callback(
                f"[Execution] 사전 매칭된 학습 매크로"
                f"({app_name}/{matched_macro}) 실행 중..."
            )
        execution_result = parser.skill_executor.execute(
            app_name,
            matched_macro,
            skill=learned_macro,
            argument=target_arg,
            slots=slots,
            source="local_match",
            action="use_learned_macro",
            target=learned_macro.get("default_target", ""),
            log_callback=log_callback,
            session_id=session_id,
            original_command=raw_user_input,
        )
        if execution_result.get("status") == "confirmation_required":
            return execution_result
        verified = bool(execution_result.get("verified", False))
        return success_result(
            f"사전에 등록된 동의어로 [{matched_macro}] 매크로를 실행했습니다.",
            action="learned_macro",
            target=matched_macro,
            verified=verified,
            verification_status=execution_result.get(
                "verification_status",
                "verified" if verified else "confirmation_required",
            ),
            data={"app": app_name, "execution_result": execution_result},
        )
    except SkillPreflightBlocked as error:
        return parser._dynamic_preflight_failure(
            error.preflight,
            action="learned_macro",
            target=matched_macro,
        )
    except SkillConfirmationRequired as error:
        return parser._queue_local_learned_dynamic_confirmation(
            app_name,
            matched_macro,
            target_arg,
            error.preflight,
            error.fingerprint,
            session_id,
            raw_user_input,
        )
    except UIAutomationAmbiguousTarget as error:
        return parser._queue_learned_uia_target_choice(
            error,
            app_name=app_name,
            macro_name=matched_macro,
            skill=learned_macro,
            slots=slots,
            session_id=session_id,
            original_command=raw_user_input,
            argument=target_arg,
            source="local_match_confirmation",
            target=learned_macro.get("default_target", ""),
        )
    except ExecutionCancelled:
        raise
    except Exception as error:
        return failure_result(
            f"사전 매칭된 매크로를 실행하다 에러가 났어 ㅠㅠ 에러: {error}",
            action="learned_macro",
            target=matched_macro,
            error_type=parser._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            data={"app": app_name},
            status=getattr(error, "status", "failed"),
        )
