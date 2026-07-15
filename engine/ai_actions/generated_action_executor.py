from engine.action_executor import (
    ActionConfirmationRequired,
    ActionPlanVerificationError,
)
from engine.execution_result import failure_result
from engine.execution_runtime import ExecutionCancelled
from engine.learning_schema import normalize_learning_metadata
from engine.ui_automation import UIAutomationAmbiguousTarget


def _learning_candidate(action, plan, app_name, macro_name, description, learning):
    return {
        "code": "",
        "plan": plan,
        "app": app_name,
        "name": macro_name,
        "desc": description,
        "steps": action.get("explanation_steps", []),
        "target": action.get("target", ""),
        "utterances": learning.get("utterances", []),
        "learning": learning,
    }


def execute_action_plan(context, action):
    parser = context.parser
    plan = action.get("plan", [])
    app_name = str(action.get("app_name") or "시스템").strip()
    macro_name = parser.skill_learning_service.make_unique_macro_name(
        app_name, action.get("macro_name")
    )
    description = action.get("description", "공통 행동 계획")
    original_utterance = (
        context.user_input if context.learnable_action_count == 1 else ""
    )
    learning = normalize_learning_metadata(action, original_utterance)
    slots = parser._build_slot_values(learning)
    candidate = _learning_candidate(
        action, plan, app_name, macro_name, description, learning
    )
    try:
        result = parser.action_executor.execute_plan(
            plan, slots, context.log_callback
        )
        context.action_results.append(result)
        verification_status = result.get(
            "verification_status", "confirmation_required"
        )
        parser.skill_learning_service.stage_candidate({
            **candidate,
            "verification_status": verification_status,
            "verification": result.get("verification", []),
        })
        if "[응/아니오]" not in context.response:
            if verification_status in {"verified", "passed"}:
                context.add_response(
                    "\n\n(실행 결과까지 자동으로 확인했어! "
                    "이 행동을 학습해서 저장할까? [응/아니오])"
                )
            else:
                context.add_response(
                    "\n\n(실행은 완료했지만 결과를 자동으로 확인할 수 없는 단계가 있어. "
                    "실제로 정확했다면 학습할까? [응/아니오])"
                )
    except ActionConfirmationRequired as error:
        context.log(f"[Confirmation] 파일 덮어쓰기 확인 필요: {error}")
        return parser._queue_action_plan_confirmation(
            error,
            {"plan": plan, "slots": slots, "learning_candidate": candidate},
            context.session_id,
            context.user_input,
        )
    except UIAutomationAmbiguousTarget as error:
        context.log(f"[Confirmation] UI 요소 후보 선택 필요: {error}")
        return parser._queue_uia_target_choice(
            error,
            {
                "plan": plan,
                "slots": slots,
                "failed_step": getattr(error, "failed_step", 1),
                "learning_candidate": candidate,
            },
            context.session_id,
            context.user_input,
        )
    except ActionPlanVerificationError as error:
        context.log(f"[Verification] 행동 계획 결과 불일치: {error}")
        context.add_response(
            f"\n\n실행 결과가 예상과 달라 학습하지 않았습니다: {error}"
        )
        context.failures.append(failure_result(
            str(error),
            action="action_plan",
            error_type="verification_error",
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
        ))
    except ExecutionCancelled:
        raise
    except Exception as error:
        context.log(f"[Error] 행동 계획 실행 실패: {error}")
        context.add_response(f"\n\n행동 계획 실행에 실패했습니다: {error}")
        context.failures.append(failure_result(
            str(error),
            action="action_plan",
            error_type=parser._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", None),
            retryable=getattr(error, "retryable", False),
            status=getattr(error, "status", "failed"),
        ))
    return None


def execute_dynamic_code(context, action):
    code = action.get("code")
    if not code:
        return None
    parser = context.parser
    app_name = str(action.get("app_name") or "시스템").strip()
    macro_name = parser.skill_learning_service.make_unique_macro_name(
        app_name, action.get("macro_name")
    )
    target = action.get("target", "")
    original_utterance = (
        context.user_input if context.learnable_action_count == 1 else ""
    )
    learning = normalize_learning_metadata(action, original_utterance)
    argument = parser._build_dynamic_argument(learning, target, app_name)
    try:
        context.log("[Execution] 동적 매크로 파이썬 코드 실행 중...")
        result = parser.macro_runner.run(code, argument)
        context.action_results.append(result)
        parser.skill_learning_service.stage_candidate({
            "code": code,
            "plan": [],
            "app": app_name,
            "name": macro_name,
            "desc": action.get("description", "동적 생성된 매크로"),
            "steps": action.get("explanation_steps", []),
            "target": target,
            "utterances": learning.get("utterances", []),
            "learning": learning,
            "verification_status": "confirmation_required",
            "verification": [],
            "candidate_execution_id": (
                parser.candidate_recording_service.execution_id()
            ),
        })
        if "[응/아니오]" not in context.response:
            context.add_response(
                "\n\n(오빠! 방금 매크로 실행을 마쳤어! "
                "잘 작동했으면 이걸 학습해서 저장할까? [응/아니오])"
            )
    except ExecutionCancelled:
        raise
    except Exception as error:
        context.log(f"[Error] 파이썬 매크로 실행 실패: {error}")
        context.add_response(
            f"\n\n으앙 ㅠㅠ 파이썬 코드 실행 중에 에러가 났어...\n에러: {error}"
        )
        context.failures.append(failure_result(
            str(error),
            action="dynamic_code",
            target=macro_name,
            error_type=parser._failure_type_for_error(error),
        ))
    return None
