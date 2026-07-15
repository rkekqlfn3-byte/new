from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.security import BLOCKED, CONFIRMATION_REQUIRED
from engine.skills.run_policy import DIRECTIVE_PREVIEW
from engine.skills.skill_executor import (
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillPreflightBlocked,
)


class LearnedReplayService:
    """Coordinate learned-skill policy, confirmation resume, and retries."""

    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, name):
        return getattr(self.owner, name)

    def resume_local_dynamic(self, payload, confirmation_id, log_callback=None):
        app_name = payload.get("app_name")
        macro_name = payload.get("macro_name")
        learned = self._get_learned_macro(app_name, macro_name)
        if not learned or not learned.get("code"):
            return failure_result(
                "확인 후 저장된 매크로를 다시 찾지 못했습니다. 실행하지 않았습니다.",
                action="learned_macro",
                target=macro_name,
                error_type="target_not_found",
            )
        argument = payload.get("argument", "")
        try:
            result = self.skill_executor.execute(
                app_name,
                macro_name,
                skill=learned,
                argument=argument,
                source="confirmation_resume",
                action="use_learned_macro",
                target=learned.get("default_target", ""),
                approved_fingerprints=payload.get("approval_fingerprints", []),
                expected_code_sha256=payload.get("expected_code_sha256", ""),
                log_callback=log_callback,
                original_command=payload.get(
                    "original_command", f"{app_name}/{macro_name} 확인 후 실행"
                ),
            )
            if result.get("status") == "confirmation_required":
                return result
            verified = bool(result.get("verified", False))
            return success_result(
                f"확인한 저장 매크로 [{macro_name}]를 이번에만 실행했습니다.",
                action="learned_macro",
                target=macro_name,
                verified=verified,
                verification_status=result.get(
                    "verification_status",
                    "verified" if verified else "confirmation_required",
                ),
                data={
                    "app": app_name,
                    "confirmation_id": confirmation_id,
                    "execution_result": result,
                },
            )
        except SkillPreflightBlocked as error:
            return self._dynamic_preflight_failure(
                error.preflight, action="learned_macro", target=macro_name
            )
        except (SkillConfirmationRequired, SkillContextChanged):
            return failure_result(
                "확인한 뒤 매크로 코드나 실행 인자가 달라져 실행하지 않았습니다. "
                "명령을 다시 요청해주세요.",
                action="learned_macro",
                target=macro_name,
                error_type="validation_error",
                status="context_changed",
            )
        except ExecutionCancelled:
            raise
        except Exception as error:
            return failure_result(
                f"확인한 저장 매크로를 실행하지 못했습니다: {error}",
                action="learned_macro",
                target=macro_name,
                error_type=self._failure_type_for_error(error),
                failed_step=getattr(error, "failed_step", None),
                retryable=getattr(error, "retryable", False),
                status=getattr(error, "status", "failed"),
            )

    @staticmethod
    def preview_result(app_name, macro_name, skill, assessment):
        plan = skill.get({
            "native": "native_plan",
            "action_plan": "plan",
            "uia": "uia_plan",
        }.get(assessment.route, ""), [])
        return success_result(
            f"[{macro_name}] 작업은 실행하지 않았습니다. 실행 경로와 정책만 미리 보여드립니다.",
            action="learned_macro_preview",
            target=macro_name,
            verified=True,
            data={
                "app": app_name,
                "macro_name": macro_name,
                "route": assessment.route,
                "run_policy": assessment.run_policy,
                "step_count": len(plan) if isinstance(plan, list) else 0,
                "forced_confirmation_reasons": list(assessment.forced_reasons),
                "executed": False,
            },
        )

    def local_policy_gate(
        self,
        *,
        app_name,
        macro_name,
        skill,
        slots,
        argument,
        session_id,
        original_command,
    ):
        assessment = self.skill_run_policy.assess(skill, original_command)
        if assessment.directive == DIRECTIVE_PREVIEW:
            return self.preview_result(app_name, macro_name, skill, assessment)
        if assessment.route == "python":
            decision = self.skill_executor.preflight(
                app_name,
                macro_name,
                skill=skill,
                argument=argument,
                action="use_learned_macro",
                target=skill.get("default_target", ""),
            )
            if decision.status == BLOCKED:
                return self._dynamic_preflight_failure(
                    decision.result,
                    action="learned_macro",
                    target=macro_name,
                )
            if decision.status == CONFIRMATION_REQUIRED:
                return self._queue_local_learned_dynamic_confirmation(
                    app_name,
                    macro_name,
                    argument,
                    decision.result,
                    decision.fingerprint,
                    session_id,
                    original_command,
                )
        if not assessment.requires_confirmation:
            return None
        return self._queue_skill_run_policy_confirmation(
            app_name=app_name,
            macro_name=macro_name,
            skill=skill,
            assessment=assessment,
            session_id=session_id,
            original_command=original_command,
            resume_payload={
                "resume_mode": "local",
                "slots": dict(slots or {}),
                "argument": argument,
                "source": "local_match_policy_resume",
                "target": skill.get("default_target", ""),
            },
        )

    def retry_step(self, app_name, macro_name, step_number):
        app_macros = getattr(self.dict_mgr, "learned_macros", {}).get(app_name, {})
        learned = app_macros.get(macro_name) if isinstance(app_macros, dict) else None
        if not isinstance(learned, dict) or not learned.get("plan"):
            raise ValueError("단계 재시도가 가능한 학습 행동을 찾지 못했습니다.")
        try:
            step_number = int(step_number)
        except (TypeError, ValueError):
            raise ValueError("재시도 단계 번호가 올바르지 않습니다.")
        self.execution_controller.begin(
            f"retry:{app_name}/{macro_name}", {"start_step": step_number}
        )
        try:
            slots = self._build_slot_values(learned.get("learning", {}))
            result = self.skill_executor.execute(
                app_name,
                macro_name,
                skill=learned,
                slots=slots,
                source="retry",
                start_step=step_number,
                retry_attempts=1,
                record_candidate=False,
            )
            self.execution_controller.finish(True, extra={"result": result})
            return result
        except ExecutionCancelled as error:
            self.execution_controller.finish(False, "cancelled", error=str(error))
            raise
        except Exception as error:
            self.execution_controller.finish(
                False,
                "failed",
                error=str(error),
                extra={
                    "failed_step": getattr(error, "failed_step", step_number),
                    "retryable": getattr(error, "retryable", False),
                },
            )
            raise
