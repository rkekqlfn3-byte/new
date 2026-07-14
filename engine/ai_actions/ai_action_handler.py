"""Validation, safety routing, and execution for AI-generated action batches."""

import os
import re

from engine.action_executor import (
    ActionConfirmationRequired,
    ActionPlanVerificationError,
)
from engine.action_registry import app_target_actions
from engine.document_reader import extract_text, resolve_file_path
from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.learning_schema import normalize_learning_metadata
from engine.security import BLOCKED, CONFIRMATION_REQUIRED
from engine.ui_automation import UIAutomationAmbiguousTarget
from engine.skills import (
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillPreflightBlocked,
)


class AIActionHandler:
    """Own the structured AI action boundary while reusing existing executors."""

    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, name):
        # Incremental extraction: existing executors and state remain parser-owned.
        return getattr(self.owner, name)

    def handle_result(
        self,
        result,
        user_input,
        *,
        log_callback=None,
        session_id=None,
        image_data=None,
        use_api=False,
    ):
        response, actions, validation_issues = self.validate_result(result)
        return self.execute_batch(
            response,
            actions,
            validation_issues,
            user_input,
            log_callback=log_callback,
            session_id=session_id,
            image_data=image_data,
            use_api=use_api,
        )

    def validate_result(self, result):
        return self._validate_llm_result(result)

    def execute_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        return self._execute_ai_action_batch(
            response,
            actions,
            validation_issues,
            user_input,
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            confirmation_id=confirmation_id,
            image_data=image_data,
            use_api=use_api,
        )

    def _ai_dynamic_descriptor(self, act):
        action = act.get("action")
        app_name = str(act.get("app_name") or "시스템").strip()
        macro_name = str(act.get("macro_name") or "dynamic_code").strip()
        target = str(act.get("target") or "")
        if action == "dynamic_code":
            learning = normalize_learning_metadata(act)
            argument = self._build_dynamic_argument(learning, target, app_name)
            code = act.get("code", "")
            label = f"새 동적 작업 {app_name}/{macro_name}"
        elif action == "adapted_macro":
            argument = act.get("_execution_argument", target)
            code = act.get("code", "")
            label = f"변형 매크로 {app_name}/{macro_name}"
        elif action == "use_learned_macro":
            learned = self._get_learned_macro(app_name, macro_name)
            if not learned or learned.get("plan") or not learned.get("code"):
                return None
            argument = act.get("_execution_argument", target)
            code = learned.get("code", "")
            label = f"저장된 매크로 {app_name}/{macro_name}"
        else:
            return None
        return {
            "action": action,
            "app_name": app_name,
            "macro_name": macro_name,
            "target": target,
            "argument": argument,
            "code": code,
            "label": label,
        }

    def _preflight_ai_action_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input_str,
        session_id,
        log_callback=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        image_data=None,
        use_api=False,
    ):
        approved = set(approved_fingerprints or [])
        approved_skills = {
            str(value).casefold() for value in (approved_skill_runs or [])
        }
        for act in actions:
            if act.get("action") != "use_learned_macro":
                continue
            app_name = str(act.get("app_name") or "시스템").strip()
            macro_name = str(act.get("macro_name") or "").strip()
            key = f"{app_name}/{macro_name}".casefold()
            if key in approved_skills:
                continue
            learned = self._get_learned_macro(app_name, macro_name)
            if not isinstance(learned, dict):
                continue
            assessment = self.skill_run_policy.assess(
                learned, user_input_str
            )
            if assessment.preview_only:
                return self._skill_policy_preview_result(
                    app_name, macro_name, learned, assessment
                )
            if assessment.route == "python":
                decision = self.skill_executor.preflight(
                    app_name,
                    macro_name,
                    skill=learned,
                    argument=str(act.get("target") or ""),
                    action="use_learned_macro",
                    target=str(act.get("target") or ""),
                    log_callback=log_callback,
                )
                if decision.status == BLOCKED:
                    return self._dynamic_preflight_failure(
                        decision.result,
                        action="use_learned_macro",
                        target=macro_name,
                    )
                if decision.status == CONFIRMATION_REQUIRED:
                    if decision.fingerprint in approved:
                        approved_skills.add(key)
                    # The common dynamic-code gate below provides the required
                    # one-shot confirmation for this Python skill.
                    continue
            if assessment.requires_confirmation:
                return self._queue_skill_run_policy_confirmation(
                    app_name=app_name,
                    macro_name=macro_name,
                    skill=learned,
                    assessment=assessment,
                    session_id=session_id,
                    original_command=user_input_str,
                    resume_payload={
                        "resume_mode": "ai_batch",
                        "response": response,
                        "actions": actions,
                        "validation_issues": validation_issues,
                        "user_input": user_input_str,
                        "image_data": image_data,
                        "use_api": bool(use_api),
                        "approval_fingerprints": sorted(approved),
                        "approved_skill_runs": sorted(approved_skills),
                    },
                )
        confirmation_items = []
        for act in actions:
            descriptor = self._ai_dynamic_descriptor(act)
            if not descriptor:
                continue
            if descriptor["action"] == "dynamic_code":
                result, fingerprint = self._analyze_dynamic_code(
                    descriptor["code"],
                    descriptor["argument"],
                    action=descriptor["action"],
                    app_name=descriptor["app_name"],
                    macro_name=descriptor["macro_name"],
                    target=descriptor["target"],
                    log_callback=log_callback,
                )
            else:
                decision = self.skill_executor.preflight(
                    descriptor["app_name"],
                    descriptor["macro_name"],
                    code_override=(
                        descriptor["code"]
                        if descriptor["action"] == "adapted_macro"
                        else None
                    ),
                    argument=descriptor["argument"],
                    action=descriptor["action"],
                    target=descriptor["target"],
                    log_callback=log_callback,
                )
                result = decision.result
                fingerprint = decision.fingerprint
            if result.status == BLOCKED:
                return self._dynamic_preflight_failure(
                    result,
                    action=descriptor["action"],
                    target=descriptor["macro_name"],
                )
            if (
                result.status == CONFIRMATION_REQUIRED
                and fingerprint not in approved
            ):
                confirmation_items.append({
                    "label": descriptor["label"],
                    "fingerprint": fingerprint,
                    "result": result,
                })
        if not confirmation_items:
            return None
        return self._queue_dynamic_code_confirmation(
            confirmation_items,
            {
                "mode": "ai_batch",
                "response": response,
                "actions": actions,
                "validation_issues": validation_issues,
                "user_input": user_input_str,
                "image_data": image_data,
                "use_api": bool(use_api),
                "approved_skill_runs": sorted(approved_skills),
            },
            session_id,
            user_input_str,
            prior_approvals=approved,
        )

    def _execute_ai_action_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input_str,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        preflight_gate = self._preflight_ai_action_batch(
            response,
            actions,
            validation_issues,
            user_input_str,
            session_id,
            log_callback=log_callback,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            image_data=image_data,
            use_api=use_api,
        )
        if preflight_gate is not None:
            return preflight_gate
        if validation_issues:
            response += "\n\n실행하지 않은 동작:\n- " + "\n- ".join(validation_issues)
        execution_failures = [failure_result(
            validation_issues[0], action="ai_validation",
            error_type="validation_error", data={"issues": validation_issues},
        )] if validation_issues else []
        action_results = []
            
        learnable_action_count = sum(
            1 for action_item in actions
            if action_item.get("action") in {"dynamic_code", "action_plan"}
        )
        for idx, act in enumerate(actions):
            action = act.get("action", "none")
            target = act.get("target")

            if log_callback: log_callback(f"[LLM] Action {idx+1}/{len(actions)}: {action}, Target: {target}")

            if action == "open_app" and target:
                path = act.get("_resolved_path")
                try:
                    if log_callback: log_callback(f"[Execution] {path} 켜는 중...")
                    os.startfile(path.replace('"', ''))
                except Exception as e:
                    if log_callback: log_callback(f"[Error] 실행 실패: {e}")
                    response += f"\n\n'{target}' 실행에 실패했습니다: {e}"
                    execution_failures.append(failure_result(
                        str(e), action="open_app", target=target,
                        error_type=self._failure_type_for_error(e), retryable=False,
                    ))
            elif action == "read_and_analyze" and target:
                if log_callback: log_callback(f"[Reader] 파일 읽는 중: {target}")
                extracted_text = extract_text(target)
                
                if "파일 읽기 실패" in extracted_text or "찾을 수 없습니다" in extracted_text:
                    response += f"\n\n으앙 ㅠㅠ 파일을 읽는 데 실패했어: {extracted_text}"
                    execution_failures.append(failure_result(
                        extracted_text, action="read_and_analyze", target=target,
                        error_type="target_not_found",
                    ))
                elif not extracted_text.strip():
                    response += "\n\n파일은 찾았지만 분석할 수 있는 텍스트가 없습니다."
                else:
                    if log_callback: log_callback("[LLM] 텍스트 추출 완료. 내용 분석 요청 중...")
                    truncated_text = extracted_text[:15000]
                    analyze_prompt = f"원래 사용자의 요청: '{user_input_str}'\n\n아래 파일 내용을 바탕으로 위 요청에 완벽하게 대답해줘.\n\n[파일 내용 시작]\n{truncated_text}\n[파일 내용 끝]"
                    analyze_result = self.llm_engine.process_command(
                        analyze_prompt, image_data, mode="question", use_api=use_api
                    )
                    if isinstance(analyze_result, dict):
                        analysis_text = analyze_result.get("response", "")
                    else:
                        analysis_text = str(analyze_result or "")
                    response += "\n\n" + (analysis_text or "내용을 분석했지만 답변이 비어 있습니다.")
                    
            elif action == "use_learned_macro":
                app_name = act.get("app_name")
                macro_name = act.get("macro_name")
                target_val = act.get("target", "")
                learned_macro = self._get_learned_macro(app_name, macro_name)
                try:
                    if not learned_macro:
                        raise KeyError(macro_name)
                    if log_callback: log_callback(f"[Execution] 학습된 매크로({app_name}/{macro_name}) 실행 중...")
                    slots = self._build_slot_values(learned_macro.get("learning", {}))
                    execution_result = self.skill_executor.execute(
                        app_name,
                        macro_name,
                        skill=learned_macro,
                        argument=target_val,
                        slots=slots,
                        source="ai_learned",
                        action="use_learned_macro",
                        target=target_val,
                        approved_fingerprints=approved_fingerprints,
                        log_callback=log_callback,
                    )
                    action_results.append(execution_result)
                    response += "\n\n(이거 예전에 오빠가 가르쳐준 매크로야! 0.1초만에 꺼내서 똑같이 실행했어! 🥰)"
                except KeyError:
                    response += f"\n\n앗, 학습된 매크로({app_name}/{macro_name})를 찾을 수 없어 ㅠㅠ 사전을 확인해줘!"
                    execution_failures.append(failure_result(
                        "학습된 매크로를 찾을 수 없습니다.",
                        action="learned_macro", target=macro_name,
                        error_type="target_not_found", data={"app": app_name},
                    ))
                except SkillPreflightBlocked as e:
                    blocked = self._dynamic_preflight_failure(
                        e.preflight, action="learned_macro", target=macro_name
                    )
                    response += f"\n\n{blocked['message']}"
                    execution_failures.append(blocked)
                except (SkillConfirmationRequired, SkillContextChanged) as e:
                    response += "\n\n학습 매크로의 안전 확인 상태가 변경되어 실행하지 않았습니다. 다시 요청해주세요."
                    execution_failures.append(failure_result(
                        str(e), action="learned_macro", target=macro_name,
                        error_type="validation_error", data={"app": app_name},
                        status="context_changed",
                    ))
                except UIAutomationAmbiguousTarget as e:
                    return self._queue_learned_uia_target_choice(
                        e,
                        app_name=app_name,
                        macro_name=macro_name,
                        skill=learned_macro,
                        slots=slots,
                        session_id=session_id,
                        original_command=user_input_str,
                        argument=target_val,
                        source="ai_learned_confirmation",
                        target=target_val,
                    )
                except ExecutionCancelled:
                    raise
                except Exception as e:
                    failure_type = self._failure_type_for_error(e)
                    response += f"\n\n으앙 ㅠㅠ 예전에 학습한 코드를 실행하다가 에러가 났어...\n에러: {e}"
                    execution_failures.append(failure_result(
                        str(e), action="learned_macro", target=macro_name,
                        error_type=failure_type,
                        failed_step=getattr(e, "failed_step", None),
                        retryable=getattr(e, "retryable", False),
                        data={"app": app_name},
                        status=getattr(e, "status", "failed"),
                    ))
                    
            elif action == "adapted_macro":
                code = act.get("code")
                app_name = act.get("app_name", "unknown")
                macro_name = act.get("macro_name", "unknown")
                target_val = act.get("target", "")
                learned_macro = self._get_learned_macro(app_name, macro_name)
                
                if code:
                    try:
                        if not learned_macro:
                            raise KeyError(macro_name)
                        if log_callback: log_callback(f"[Execution] 학습된 매크로 응용 중 ({app_name}/{macro_name})...")
                        execution_result = self.skill_executor.execute(
                            app_name,
                            macro_name,
                            skill=learned_macro,
                            code_override=code,
                            argument=target_val,
                            source="adapted_macro",
                            action="adapted_macro",
                            target=target_val,
                            approved_fingerprints=approved_fingerprints,
                            log_callback=log_callback,
                            record_candidate=False,
                        )
                        action_results.append(execution_result)
                        response += f"\n\n(오빠! 예전에 가르쳐준 매크로 [{app_name}/{macro_name}] 에서 단어만 살짝 바꿔서 응용해 봤어! 똑똑하지? 🥰)"
                    except KeyError:
                        response += f"\n\n응용할 학습 매크로({app_name}/{macro_name})를 찾지 못했습니다."
                        execution_failures.append(failure_result(
                            "응용할 학습 매크로를 찾을 수 없습니다.",
                            action="adapted_macro", target=macro_name,
                            error_type="target_not_found", data={"app": app_name},
                        ))
                    except SkillPreflightBlocked as e:
                        blocked = self._dynamic_preflight_failure(
                            e.preflight, action="adapted_macro", target=macro_name
                        )
                        response += f"\n\n{blocked['message']}"
                        execution_failures.append(blocked)
                    except (SkillConfirmationRequired, SkillContextChanged) as e:
                        response += "\n\n응용 매크로의 안전 확인 상태가 변경되어 실행하지 않았습니다. 다시 요청해주세요."
                        execution_failures.append(failure_result(
                            str(e), action="adapted_macro", target=macro_name,
                            error_type="validation_error", data={"app": app_name},
                            status="context_changed",
                        ))
                    except ExecutionCancelled:
                        raise
                    except Exception as e:
                        failure_type = self._failure_type_for_error(e)
                        if log_callback: log_callback(f"[Error] 매크로 응용 실행 실패: {e}")
                        response += f"\n\n으앙 ㅠㅠ 배운 매크로를 응용해보려고 했는데 에러가 났어...\n에러: {e}"
                        execution_failures.append(failure_result(
                            str(e), action="adapted_macro", target=macro_name,
                            error_type=failure_type,
                            failed_step=getattr(e, "failed_step", None),
                            retryable=getattr(e, "retryable", False),
                            data={"app": app_name},
                            status=getattr(e, "status", "failed"),
                        ))
                    
            elif action == "action_plan":
                plan = act.get("plan", [])
                app_name = str(act.get("app_name") or "시스템").strip()
                macro_name = self.skill_learning_service.make_unique_macro_name(
                    app_name, act.get("macro_name")
                )
                description = act.get("description", "공통 행동 계획")
                target_val = act.get("target", "")
                original_utterance = user_input_str if learnable_action_count == 1 else ""
                learning = normalize_learning_metadata(act, original_utterance)
                slots = self._build_slot_values(learning)
                try:
                    execution_result = self.action_executor.execute_plan(
                        plan, slots, log_callback
                    )
                    action_results.append(execution_result)
                    verification_status = execution_result.get(
                        "verification_status", "confirmation_required"
                    )
                    self.skill_learning_service.stage_candidate({
                        "code": "", "plan": plan, "app": app_name, "name": macro_name,
                        "desc": description, "steps": act.get("explanation_steps", []),
                        "target": target_val, "utterances": learning.get("utterances", []),
                        "learning": learning,
                        "verification_status": verification_status,
                        "verification": execution_result.get("verification", []),
                    })
                    if "[응/아니오]" not in response:
                        if verification_status in {"verified", "passed"}:
                            response += "\n\n(실행 결과까지 자동으로 확인했어! 이 행동을 학습해서 저장할까? [응/아니오])"
                        else:
                            response += "\n\n(실행은 완료했지만 결과를 자동으로 확인할 수 없는 단계가 있어. 실제로 정확했다면 학습할까? [응/아니오])"
                except ActionConfirmationRequired as e:
                    if log_callback:
                        log_callback(f"[Confirmation] 파일 덮어쓰기 확인 필요: {e}")
                    return self._queue_action_plan_confirmation(
                        e,
                        {
                            "plan": plan,
                            "slots": slots,
                            "learning_candidate": {
                                "code": "",
                                "plan": plan,
                                "app": app_name,
                                "name": macro_name,
                                "desc": description,
                                "steps": act.get("explanation_steps", []),
                                "target": target_val,
                                "utterances": learning.get("utterances", []),
                                "learning": learning,
                            },
                        },
                        session_id,
                        user_input_str,
                    )
                except UIAutomationAmbiguousTarget as e:
                    if log_callback:
                        log_callback(
                            f"[Confirmation] UI 요소 후보 선택 필요: {e}"
                        )
                    return self._queue_uia_target_choice(
                        e,
                        {
                            "plan": plan,
                            "slots": slots,
                            "failed_step": getattr(e, "failed_step", 1),
                            "learning_candidate": {
                                "code": "",
                                "plan": plan,
                                "app": app_name,
                                "name": macro_name,
                                "desc": description,
                                "steps": act.get("explanation_steps", []),
                                "target": target_val,
                                "utterances": learning.get("utterances", []),
                                "learning": learning,
                            },
                        },
                        session_id,
                        user_input_str,
                    )
                except ActionPlanVerificationError as e:
                    if log_callback: log_callback(f"[Verification] 행동 계획 결과 불일치: {e}")
                    response += f"\n\n실행 결과가 예상과 달라 학습하지 않았습니다: {e}"
                    execution_failures.append(failure_result(
                        str(e), action="action_plan",
                        error_type="verification_error",
                        failed_step=getattr(e, "failed_step", None),
                        retryable=getattr(e, "retryable", False),
                        status=getattr(e, "status", "failed"),
                    ))
                except ExecutionCancelled:
                    raise
                except Exception as e:
                    if log_callback: log_callback(f"[Error] 행동 계획 실행 실패: {e}")
                    response += f"\n\n행동 계획 실행에 실패했습니다: {e}"
                    execution_failures.append(failure_result(
                        str(e), action="action_plan",
                        error_type=self._failure_type_for_error(e),
                        failed_step=getattr(e, "failed_step", None),
                        retryable=getattr(e, "retryable", False),
                        status=getattr(e, "status", "failed"),
                    ))

            elif action == "dynamic_code":
                code = act.get("code")
                app_name = str(act.get("app_name") or "시스템").strip()
                macro_name = self.skill_learning_service.make_unique_macro_name(
                    app_name, act.get("macro_name")
                )
                description = act.get("description", "동적 생성된 매크로")
                target_val = act.get("target", "")
                
                if code:
                    original_utterance = user_input_str if learnable_action_count == 1 else ""
                    learning = normalize_learning_metadata(act, original_utterance)
                    execution_arg = self._build_dynamic_argument(
                        learning, target_val, app_name
                    )
                    try:
                        if log_callback: log_callback("[Execution] 동적 매크로 파이썬 코드 실행 중...")
                        execution_result = self.macro_runner.run(code, execution_arg)
                        action_results.append(execution_result)
                        
                        self.skill_learning_service.stage_candidate({
                            "code": code,
                            "plan": [],
                            "app": app_name,
                            "name": macro_name,
                            "desc": description,
                            "steps": act.get("explanation_steps", []),
                            "target": target_val,
                            # A whole compound sentence must not be attached to each
                            # individual action. Compound templates are handled later.
                            "utterances": learning.get("utterances", []),
                            "learning": learning,
                            "verification_status": "confirmation_required",
                            "verification": [],
                            "candidate_execution_id": (
                                self.candidate_recording_service.execution_id()
                            ),
                        })
                        
                        # 다단계 명령일 경우 마지막 동적 코드에 대해서만 물어보도록 처리
                        if "[응/아니오]" not in response:
                            response += "\n\n(오빠! 방금 매크로 실행을 마쳤어! 잘 작동했으면 이걸 학습해서 저장할까? [응/아니오])"
                    except ExecutionCancelled:
                        raise
                    except Exception as e:
                        if log_callback: log_callback(f"[Error] 파이썬 매크로 실행 실패: {e}")
                        response += f"\n\n으앙 ㅠㅠ 파이썬 코드 실행 중에 에러가 났어...\n에러: {e}"
                        execution_failures.append(failure_result(
                            str(e), action="dynamic_code", target=macro_name,
                            error_type=self._failure_type_for_error(e),
                        ))
            
            # 앱 실행 직후 후속 자동화가 이어질 때만 짧게 준비 시간을 준다.
            if action == "open_app" and idx < len(actions) - 1:
                self.execution_controller.wait(0.15)

        if execution_failures:
            first = execution_failures[0]
            return failure_result(
                response, action=first.get("action", "ai_command"),
                target=first.get("target"),
                error_type=first.get("error_type", "execution_error"),
                failed_step=first.get("failed_step"),
                retryable=first.get("retryable", False),
                data={"failures": execution_failures},
                status=first.get("status", "failed"),
            )
        verified = bool(action_results) and all(
            item.get("success", False) and item.get("verified", False)
            for item in action_results
        )
        result_data = {
            "action_count": len(actions),
            "action_results": action_results,
        }
        if confirmation_id:
            result_data["confirmation_id"] = confirmation_id
        return success_result(
            response, action="ai_command", verified=verified,
            verification_status=(
                "verified" if verified else "confirmation_required"
            ),
            data=result_data,
        )

    @staticmethod
    def _ensure_input_focus_steps(plan):
        """Bind keyboard input to an app and insert a verified focus step."""
        if not isinstance(plan, list):
            return plan
        normalized = []
        active_target = ""
        focused_target = ""
        for raw_step in plan:
            if not isinstance(raw_step, dict):
                normalized.append(raw_step)
                continue
            step = dict(raw_step)
            action = step.get("action")
            target = str(step.get("target", "") or "").strip()
            if action == "open_app":
                active_target = target
                focused_target = ""
            elif action == "focus_window":
                active_target = target
                focused_target = target
            elif action in {"move_window", "window_state"} and target:
                active_target = target
                if focused_target != target:
                    focused_target = ""
            elif action in {"hotkey", "type_text"}:
                input_target = target or active_target
                if input_target:
                    step["target"] = input_target
                    if focused_target != input_target:
                        normalized.append({
                            "action": "focus_window",
                            "target": input_target,
                            "direction": "",
                            "keys": [],
                            "text": "",
                            "seconds": 0,
                            "x": 0,
                            "y": 0,
                            "width": 0,
                            "height": 0,
                            "_implicit": "input_focus",
                        })
                    active_target = input_target
                    focused_target = input_target
            normalized.append(step)
        return normalized

    def _validate_llm_result(self, result):
        default_response = "으응? 무슨 말인지 잘 못 알아들었어.. 단어나 동의어를 사전에 먼저 등록해줄래? 🥺"
        issues = []

        if not isinstance(result, dict):
            response = str(result).strip() if result else default_response
            return response, [], ["AI 응답이 올바른 JSON 객체 형식이 아닙니다."]

        enforce_app_candidates = "_allowed_app_candidates" in result
        allowed_apps = {
            str(name).strip().casefold()
            for name in (result.get("_allowed_app_candidates") or [])
            if str(name).strip()
        }
        enforce_macro_candidates = "_allowed_learned_candidates" in result
        allowed_macros = {
            (str(pair[0]).strip().casefold(), str(pair[1]).strip().casefold())
            for pair in (result.get("_allowed_learned_candidates") or [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        }

        response = result.get("response", default_response)
        if not isinstance(response, str):
            response = str(response) if response is not None else default_response

        raw_actions = result.get("actions")
        if raw_actions is None and result.get("action"):
            raw_actions = [result]
        if raw_actions is None:
            raw_actions = []
        if not isinstance(raw_actions, list):
            return response, [], ["actions 값이 배열 형식이 아닙니다."]

        if len(raw_actions) > 8:
            issues.append("한 번에 실행 가능한 동작은 최대 8개입니다. 초과 동작은 제외했습니다.")
            raw_actions = raw_actions[:8]

        valid_actions = []
        allowed_actions = {
            "open_app", "action_plan", "dynamic_code", "use_learned_macro",
            "adapted_macro", "read_and_analyze", "none"
        }

        for index, raw_action in enumerate(raw_actions, start=1):
            prefix = f"{index}번 동작"
            if not isinstance(raw_action, dict):
                issues.append(f"{prefix}: 객체 형식이 아닙니다.")
                continue

            act = dict(raw_action)
            action = act.get("action")
            if action not in allowed_actions:
                issues.append(f"{prefix}: 알 수 없는 action '{action}'입니다.")
                continue

            for field, limit in (
                ("target", 500), ("app_name", 100), ("macro_name", 100),
                ("description", 500),
            ):
                if field in act and not isinstance(act[field], str):
                    act[field] = str(act[field] or "")
                if isinstance(act.get(field), str):
                    act[field] = act[field].strip()[:limit]

            if action == "none":
                valid_actions.append({"action": "none"})
                continue

            if action == "open_app":
                resolved = self._resolve_registered_app(act.get("target"))
                if not resolved:
                    issues.append(f"{prefix}: 등록된 앱 또는 웹사이트 target이 아닙니다.")
                    continue
                noun, path = resolved
                if enforce_app_candidates and noun.casefold() not in allowed_apps:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 앱 또는 웹사이트입니다.")
                    continue
                act["target"] = noun
                act["_resolved_path"] = path

            elif action == "read_and_analyze":
                resolved_path = resolve_file_path(act.get("target"))
                if not resolved_path:
                    issues.append(f"{prefix}: 실제로 존재하는 파일 경로를 찾을 수 없습니다.")
                    continue
                act["target"] = resolved_path

            elif action == "use_learned_macro":
                learned = self._get_learned_macro(act.get("app_name"), act.get("macro_name"))
                if not learned or not (learned.get("code") or learned.get("plan")):
                    issues.append(f"{prefix}: 등록된 학습 매크로를 찾을 수 없습니다.")
                    continue
                macro_key = (
                    act.get("app_name", "").casefold(),
                    act.get("macro_name", "").casefold(),
                )
                if enforce_macro_candidates and macro_key not in allowed_macros:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 학습 매크로입니다.")
                    continue

            elif action == "adapted_macro":
                learned = self._get_learned_macro(act.get("app_name"), act.get("macro_name"))
                if not learned:
                    issues.append(f"{prefix}: 응용할 원본 학습 매크로를 찾을 수 없습니다.")
                    continue
                macro_key = (
                    act.get("app_name", "").casefold(),
                    act.get("macro_name", "").casefold(),
                )
                if enforce_macro_candidates and macro_key not in allowed_macros:
                    issues.append(f"{prefix}: AI 요청 후보에 없던 학습 매크로입니다.")
                    continue
                code_issue = self._validate_generated_code(act)
                if code_issue:
                    issues.append(f"{prefix}: {code_issue}")
                    continue

            elif action == "dynamic_code":
                code_issue = self._validate_generated_code(act, require_external_target=True)
                if code_issue:
                    issues.append(f"{prefix}: {code_issue}")
                    continue

            elif action == "action_plan":
                raw_learning = act.get("learning", {})
                if not isinstance(raw_learning, dict):
                    issues.append(f"{prefix}: learning 값이 객체 형식이 아닙니다.")
                    continue
                learning = normalize_learning_metadata(act)
                required_learning = {
                    "verbs": learning.get("verbs"),
                    "utterances": learning.get("utterances"),
                }
                missing_learning = [
                    name for name, value in required_learning.items() if not value
                ]
                if not str(act.get("macro_name", "")).strip():
                    missing_learning.append("macro_name")
                if missing_learning:
                    issues.append(
                        f"{prefix}: 학습 정보가 비어 있습니다: {', '.join(missing_learning)}"
                    )
                    continue
                act["learning"] = learning
                act["plan"] = self._ensure_input_focus_steps(act.get("plan"))
                slot_names = [
                    item.get("name") for item in learning.get("slots", [])
                    if isinstance(item, dict) and item.get("name")
                ]
                plan_issue = self.action_executor.validate_plan(
                    act.get("plan"), slot_names=slot_names
                )
                if plan_issue:
                    issues.append(f"{prefix}: {plan_issue}")
                    continue
                if enforce_app_candidates:
                    candidate_issue = self._validate_plan_app_candidates(
                        act.get("plan"), learning, allowed_apps
                    )
                    if candidate_issue:
                        issues.append(f"{prefix}: {candidate_issue}")
                        continue

            valid_actions.append(act)

        return response, valid_actions, issues

    def _validate_plan_app_candidates(self, plan, learning, allowed_apps):
        slot_values = {
            str(item.get("name", "")): str(item.get("value", ""))
            for item in learning.get("slots", [])
            if isinstance(item, dict) and item.get("name")
        }
        for index, step in enumerate(plan or [], start=1):
            if not isinstance(step, dict) or step.get("action") not in app_target_actions():
                continue
            target = str(step.get("target", "")).strip()
            target = re.sub(
                r"\{([0-9a-zA-Z가-힣_]+)\}",
                lambda match: slot_values.get(match.group(1), match.group(0)),
                target,
            )
            if "{" in target:
                continue
            resolved = self._resolve_registered_app(target)
            if resolved and resolved[0].casefold() not in allowed_apps:
                return f"행동 계획 {index}단계가 AI 요청 후보에 없던 앱을 사용합니다."
        return None

    def _validate_generated_code(self, act, require_external_target=False):
        code = act.get("code")
        if not isinstance(code, str) or not code.strip():
            return "실행할 파이썬 code가 비어 있습니다."

        steps = act.get("explanation_steps")
        if not isinstance(steps, list) or not steps:
            return "explanation_steps가 비어 있습니다."
        for step in steps:
            if (
                not isinstance(step, dict)
                or not str(step.get("step", "")).strip()
                or not str(step.get("code_snippet", "")).strip()
            ):
                return "explanation_steps 항목 형식이 올바르지 않습니다."

        try:
            compile(code, "<jarvis-generated-code>", "exec")
        except SyntaxError as error:
            return f"파이썬 문법 오류가 있습니다: {error.msg} (줄 {error.lineno})"
        descriptor = " ".join(
            str(act.get(field, ""))
            for field in ("target", "app_name", "macro_name", "description")
        ).casefold()
        excel_specific = (
            "excel" in descriptor
            or "엑셀" in descriptor
            or "excel.application" in code.casefold()
        )
        if excel_specific and re.search(
            r"\bGetForegroundWindow\s*\(", code, flags=re.IGNORECASE
        ):
            return (
                "Excel 창 제목은 현재 전면 창 API로 읽을 수 없습니다. "
                "win32com.client.GetActiveObject('Excel.Application')의 "
                "ActiveWindow.Caption을 사용해야 합니다."
            )
        learning = act.get("learning", {})
        if require_external_target:
            if (
                not isinstance(learning, dict)
                or learning.get("argument_mode") != "json"
                or not isinstance(learning.get("slots"), list)
            ):
                return (
                    "동적 코드는 learning.argument_mode=json과 slots 배열을 제공해야 하며, "
                    "슬롯 값은 sys.argv로 받아야 합니다."
                )
            has_slots = any(
                isinstance(item, dict) and str(item.get("name", "")).strip()
                for item in learning.get("slots", [])
            )
            if has_slots and "sys.argv" not in code:
                return "슬롯이 있는 동적 코드는 값을 sys.argv로 받아야 합니다."
            if has_slots and "json.loads" not in code:
                return "JSON 슬롯 매크로는 json.loads(sys.argv[1])로 인자를 읽어야 합니다."
        return None
