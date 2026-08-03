from engine.execution_result import failure_result
from engine.execution_runtime import ExecutionCancelled
from engine.skills import (
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillPreflightBlocked,
)
from engine.ui_automation import UIAutomationAmbiguousTarget


def execute_learned_action(context, action):
    parser = context.parser
    app_name = action.get("app_name")
    macro_name = action.get("macro_name")
    target = action.get("target", "")
    skill = parser._get_learned_macro(app_name, macro_name)
    slots = {}
    try:
        if not skill:
            raise KeyError(macro_name)
        context.log(f"[Execution] 학습된 매크로({app_name}/{macro_name}) 실행 중...")
        slots = parser._build_slot_values(skill.get("learning", {}))
        result = parser.skill_executor.execute(
            app_name,
            macro_name,
            skill=skill,
            argument=target,
            slots=slots,
            source="ai_learned",
            action="use_learned_macro",
            target=target,
            approved_fingerprints=context.approved_fingerprints,
            log_callback=context.log_callback,
            session_id=context.session_id,
            original_command=context.user_input,
        )
        if result.get("status") == "confirmation_required":
            return result
        context.action_results.append(result)
        context.add_response(
            "\n\n(이거 예전에 오빠가 가르쳐준 매크로야! "
            "0.1초만에 꺼내서 똑같이 실행했어! 🥰)"
        )
    except KeyError:
        context.add_response(
            f"\n\n앗, 학습된 매크로({app_name}/{macro_name})를 찾을 수 없어 "
            "ㅠㅠ 사전을 확인해줘!"
        )
        context.failures.append(failure_result(
            "학습된 매크로를 찾을 수 없습니다.",
            action="learned_macro",
            target=macro_name,
            error_type="target_not_found",
            data={"app": app_name},
        ))
    except SkillPreflightBlocked as error:
        blocked = parser._dynamic_preflight_failure(
            error.preflight, action="learned_macro", target=macro_name
        )
        context.add_response(f"\n\n{blocked['message']}")
        context.failures.append(blocked)
    except (SkillConfirmationRequired, SkillContextChanged) as error:
        context.add_response(
            "\n\n학습 매크로의 안전 확인 상태가 변경되어 실행하지 않았습니다. "
            "다시 요청해주세요."
        )
        context.failures.append(failure_result(
            str(error),
            action="learned_macro",
            target=macro_name,
            error_type="validation_error",
            data={"app": app_name},
            status="context_changed",
        ))
    except UIAutomationAmbiguousTarget as error:
        return parser.confirmations.queue_learned_uia_target(
            parser,
            error,
            app_name=app_name,
            macro_name=macro_name,
            skill=skill,
            slots=slots,
            session_id=context.session_id,
            original_command=context.user_input,
            argument=target,
            source="ai_learned_confirmation",
            target=target,
        )
    except ExecutionCancelled:
        raise
    except Exception as error:
        context.add_response(
            "\n\n으앙 ㅠㅠ 예전에 학습한 코드를 실행하다가 에러가 났어..."
            f"\n에러: {error}"
        )
        context.failures.append(failure_result(
            str(error),
            action="learned_macro",
            target=macro_name,
            error_type=parser._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            data={"app": app_name},
            status=getattr(error, "status", "failed"),
        ))
    return None


def execute_adapted_action(context, action):
    code = action.get("code")
    if not code:
        return None
    parser = context.parser
    app_name = action.get("app_name", "unknown")
    macro_name = action.get("macro_name", "unknown")
    target = action.get("target", "")
    skill = parser._get_learned_macro(app_name, macro_name)
    try:
        if not skill:
            raise KeyError(macro_name)
        context.log(f"[Execution] 학습된 매크로 응용 중 ({app_name}/{macro_name})...")
        result = parser.skill_executor.execute(
            app_name,
            macro_name,
            skill=skill,
            code_override=code,
            argument=target,
            source="adapted_macro",
            action="adapted_macro",
            target=target,
            approved_fingerprints=context.approved_fingerprints,
            log_callback=context.log_callback,
            record_candidate=False,
            session_id=context.session_id,
            original_command=context.user_input,
        )
        if result.get("status") == "confirmation_required":
            return result
        context.action_results.append(result)
        context.add_response(
            f"\n\n(오빠! 예전에 가르쳐준 매크로 [{app_name}/{macro_name}] 에서 "
            "단어만 살짝 바꿔서 응용해 봤어! 똑똑하지? 🥰)"
        )
    except KeyError:
        context.add_response(
            f"\n\n응용할 학습 매크로({app_name}/{macro_name})를 찾지 못했습니다."
        )
        context.failures.append(failure_result(
            "응용할 학습 매크로를 찾을 수 없습니다.",
            action="adapted_macro",
            target=macro_name,
            error_type="target_not_found",
            data={"app": app_name},
        ))
    except SkillPreflightBlocked as error:
        blocked = parser._dynamic_preflight_failure(
            error.preflight, action="adapted_macro", target=macro_name
        )
        context.add_response(f"\n\n{blocked['message']}")
        context.failures.append(blocked)
    except (SkillConfirmationRequired, SkillContextChanged) as error:
        context.add_response(
            "\n\n응용 매크로의 안전 확인 상태가 변경되어 실행하지 않았습니다. "
            "다시 요청해주세요."
        )
        context.failures.append(failure_result(
            str(error),
            action="adapted_macro",
            target=macro_name,
            error_type="validation_error",
            data={"app": app_name},
            status="context_changed",
        ))
    except ExecutionCancelled:
        raise
    except Exception as error:
        context.log(f"[Error] 매크로 응용 실행 실패: {error}")
        context.add_response(
            "\n\n으앙 ㅠㅠ 배운 매크로를 응용해보려고 했는데 에러가 났어..."
            f"\n에러: {error}"
        )
        context.failures.append(failure_result(
            str(error),
            action="adapted_macro",
            target=macro_name,
            error_type=parser._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            data={"app": app_name},
            status=getattr(error, "status", "failed"),
        ))
    return None
