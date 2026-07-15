import os

from engine.action_executor import (
    ActionConfirmationRequired,
    ActionPlanVerificationError,
)
from engine.document_reader import extract_text
from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.learning_schema import normalize_learning_metadata
from engine.skills import (
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillPreflightBlocked,
)
from engine.ui_automation import UIAutomationAmbiguousTarget


class BatchExecutor:
    def __init__(self, owner):
        self.owner = owner

    def execute(
        self,
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
        parser = self.owner
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
                        error_type=parser._failure_type_for_error(e), retryable=False,
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
                    analyze_result = parser.llm_engine.process_command(
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
                learned_macro = parser._get_learned_macro(app_name, macro_name)
                try:
                    if not learned_macro:
                        raise KeyError(macro_name)
                    if log_callback: log_callback(f"[Execution] 학습된 매크로({app_name}/{macro_name}) 실행 중...")
                    slots = parser._build_slot_values(learned_macro.get("learning", {}))
                    execution_result = parser.skill_executor.execute(
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
                        session_id=session_id,
                        original_command=user_input_str,
                    )
                    if execution_result.get("status") == "confirmation_required":
                        return execution_result
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
                    blocked = parser._dynamic_preflight_failure(
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
                    return parser._queue_learned_uia_target_choice(
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
                    failure_type = parser._failure_type_for_error(e)
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
                learned_macro = parser._get_learned_macro(app_name, macro_name)

                if code:
                    try:
                        if not learned_macro:
                            raise KeyError(macro_name)
                        if log_callback: log_callback(f"[Execution] 학습된 매크로 응용 중 ({app_name}/{macro_name})...")
                        execution_result = parser.skill_executor.execute(
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
                            session_id=session_id,
                            original_command=user_input_str,
                        )
                        if execution_result.get("status") == "confirmation_required":
                            return execution_result
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
                        blocked = parser._dynamic_preflight_failure(
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
                        failure_type = parser._failure_type_for_error(e)
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
                macro_name = parser.skill_learning_service.make_unique_macro_name(
                    app_name, act.get("macro_name")
                )
                description = act.get("description", "공통 행동 계획")
                target_val = act.get("target", "")
                original_utterance = user_input_str if learnable_action_count == 1 else ""
                learning = normalize_learning_metadata(act, original_utterance)
                slots = parser._build_slot_values(learning)
                try:
                    execution_result = parser.action_executor.execute_plan(
                        plan, slots, log_callback
                    )
                    action_results.append(execution_result)
                    verification_status = execution_result.get(
                        "verification_status", "confirmation_required"
                    )
                    parser.skill_learning_service.stage_candidate({
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
                    return parser._queue_action_plan_confirmation(
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
                    return parser._queue_uia_target_choice(
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
                        error_type=parser._failure_type_for_error(e),
                        failed_step=getattr(e, "failed_step", None),
                        retryable=getattr(e, "retryable", False),
                        status=getattr(e, "status", "failed"),
                    ))

            elif action == "dynamic_code":
                code = act.get("code")
                app_name = str(act.get("app_name") or "시스템").strip()
                macro_name = parser.skill_learning_service.make_unique_macro_name(
                    app_name, act.get("macro_name")
                )
                description = act.get("description", "동적 생성된 매크로")
                target_val = act.get("target", "")

                if code:
                    original_utterance = user_input_str if learnable_action_count == 1 else ""
                    learning = normalize_learning_metadata(act, original_utterance)
                    execution_arg = parser._build_dynamic_argument(
                        learning, target_val, app_name
                    )
                    try:
                        if log_callback: log_callback("[Execution] 동적 매크로 파이썬 코드 실행 중...")
                        execution_result = parser.macro_runner.run(code, execution_arg)
                        action_results.append(execution_result)

                        parser.skill_learning_service.stage_candidate({
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
                                parser.candidate_recording_service.execution_id()
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
                            error_type=parser._failure_type_for_error(e),
                        ))

            # 앱 실행 직후 후속 자동화가 이어질 때만 짧게 준비 시간을 준다.
            if action == "open_app" and idx < len(actions) - 1:
                parser.execution_controller.wait(0.15)

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
