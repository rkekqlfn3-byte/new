import os

from engine.execution_result import failure_result, success_result
from engine.execution_runtime import ExecutionCancelled
from engine.skills import (
    SkillConfirmationRequired,
    SkillPreflightBlocked,
    strip_run_directive,
)
from engine.ui_automation import UIAutomationAmbiguousTarget


class CommandPipeline:
    """Route one command through local, native-app, learned, and AI stages."""

    def __init__(self, owner):
        self.owner = owner

    def execute(
        self,
        user_input,
        log_callback=None,
        image_data=None,
        mode="command",
        use_api=False,
        summary="",
        stream_callback=None,
        conversation_state=None,
        session_id=None,
    ):
        parser = self.owner
        parser.action_executor.noun_dict = parser.dict_mgr.noun_dict
        # 1. 대화/질문 모드 (Conversation/Question Mode) - AI 엔진(Ollama/API) 사용
        if mode in ["conversation", "question"]:
            latest_user_text = parser._latest_user_text(user_input)
            if parser._is_current_date_question(latest_user_text):
                if log_callback:
                    log_callback("[Parser] 현재 날짜 질문을 로컬 시스템 시계로 처리합니다.")
                return parser.builtins.handle_date()

            if log_callback: log_callback(f"[Parser] {mode} 모드(AI) 실행 중... (history length: {len(user_input) if isinstance(user_input, list) else 1})")

            result = parser.llm_engine.process_command(user_input, image_data, mode=mode, use_api=use_api, summary=summary, stream_callback=stream_callback, conversation_state=conversation_state)

            if not isinstance(result, dict):
                result = {"response": str(result) if result else "AI가 빈 응답을 반환했습니다."}
            response = result.get("response", "으앙... 대답을 만들다 꼬였어 ㅠㅠ")
            if result.get("provider_error"):
                return failure_result(
                    response, action="ai_provider",
                    target=result.get("provider"), error_type="environment_error",
                    retryable=True,
                )
            if result.get("no_api_key"):
                return failure_result(
                    response, action="ai_provider",
                    target=result.get("provider"), error_type="validation_error",
                )
            action = result.get("action", "none")
            target = result.get("target")

            if log_callback: log_callback(f"[LLM] Action: {action}, Target: {target}")

            conversation_error = None
            if action == "open_app" and target:
                path = parser.dict_mgr.noun_dict.get(target)
                if path:
                    try:
                        if log_callback: log_callback(f"[Execution] {path} 켜는 중...")
                        os.startfile(path.replace('"', ''))
                    except Exception as e:
                        if log_callback: log_callback(f"[Error] 실행 실패: {e}")
                        conversation_error = e
                else:
                    return failure_result(
                        f"등록된 앱을 찾지 못했습니다: {target}",
                        action="open_app", target=target,
                        error_type="target_not_found",
                    )

            if conversation_error:
                return failure_result(
                    response, action="open_app", target=target,
                    error_type=parser._failure_type_for_error(conversation_error),
                    retryable=False, data={"error": str(conversation_error)},
                )
            return success_result(response, action="conversation", verified=False)

        # 2. 명령 모드 (Command Mode) - AI 없이 초고속 텍스트 매칭 (기존 하드코딩 매크로)
        if isinstance(user_input, list):
            user_input = user_input[-1].get("content", "") if user_input else ""
        raw_user_input_str = str(user_input or "").strip()
        user_input_str = raw_user_input_str.lower()

        # Check if waiting for learning approval
        if getattr(self, "pending_macros", []):
            approval_words = {"예", "네", "y", "yes", "응", "학습", "저장", "맞아", "그래", "ㅇㅇ", "ㅇ"}
            rejection_words = {"아니", "아니오", "아니요", "틀렸어", "취소", "버려", "ㄴㄴ", "ㄴ", "하지마"}
            if user_input_str in approval_words:
                return parser.approve_pending_learning()
            if user_input_str in rejection_words:
                return parser.reject_pending_learning()
            return failure_result(
                "학습 검토가 대기 중입니다. 검토 화면에서 저장·이번만 실행·폐기 중 하나를 선택해주세요.",
                action="learning_review", error_type="validation_error",
                status="confirmation_required",
            )

        if user_input_str in {"확인 카드 테스트", "확인카드 테스트"}:
            return parser._queue_demo_confirmation(user_input_str, session_id)

        native_excel_request = (
            parser._parse_native_excel_write_command(raw_user_input_str)
            or parser._parse_native_excel_sum_command(raw_user_input_str)
            or parser._parse_native_excel_format_command(raw_user_input_str)
            or parser._parse_native_excel_range_format_command(raw_user_input_str)
            or parser._parse_native_excel_filter_command(raw_user_input_str)
            or parser._parse_native_excel_find_replace_command(raw_user_input_str)
            or parser._parse_native_excel_sort_command(raw_user_input_str)
        )
        native_hwp_request = (
            parser._parse_native_hwp_find_replace_command(raw_user_input_str)
            or parser._parse_native_hwp_text_format_command(raw_user_input_str)
            or parser._parse_native_hwp_paragraph_format_command(raw_user_input_str)
            or parser._parse_native_hwp_insert_command(raw_user_input_str)
            or parser._parse_native_hwp_save_command(raw_user_input_str)
        )
        native_app_request = native_excel_request or native_hwp_request
        if native_app_request:
            if native_app_request.get("operation") == "choose_format_method":
                return parser._handle_format_method_request(
                    native_app_request,
                    session_id,
                    raw_user_input_str,
                    log_callback=log_callback,
                )
            if native_app_request.get("operation") == "choose_hwp_replace_scope":
                return parser._queue_hwp_scope_choice(
                    native_app_request,
                    session_id,
                    raw_user_input_str,
                    log_callback=log_callback,
                )
            return parser._execute_native_app_command(
                native_app_request,
                session_id,
                raw_user_input_str,
                log_callback=log_callback,
            )

        routing_mode = parser.dict_mgr.get_ai_config().get("routing_mode", "auto")
        if routing_mode not in {"auto", "local_only", "ai_first"}:
            routing_mode = "auto"

        analysis = parser.analyze_command(strip_run_directive(user_input_str))
        force_ai_fallback = (
            analysis["kind"] == "compound" or routing_mode == "ai_first"
        )
        if force_ai_fallback:
            compound_parts = (
                [step["text"] for step in analysis["steps"]]
                if analysis["kind"] == "compound" else []
            )
            if (
                analysis["kind"] == "compound"
                and analysis["executable"]
                and routing_mode != "ai_first"
            ):
                return parser._execute_local_compound(
                    compound_parts, log_callback, image_data, use_api
                )
            if log_callback and analysis["kind"] == "compound":
                log_callback("[Parser] 복합 명령 일부를 로컬에서 해석할 수 없어 전체 문장을 AI로 전달합니다.")
            elif log_callback and routing_mode == "ai_first":
                log_callback("[Router] AI 우선 모드로 명령 전체를 AI에 전달합니다.")

        normalized_tokens = analysis.get("tokens", parser.normalize_text(user_input_str))
        matched_macro = analysis.get("macro")
        matched_app_name = analysis.get("app_name")
        matched_app_path = analysis.get("app_path")
        template_match = analysis.get("template_match")

        if force_ai_fallback:
            matched_macro = None

        if log_callback:
            log_callback(f"[Parser] 매크로: {matched_macro}, 앱: {matched_app_name}")

        # Execute Macro
        if matched_macro:
            macro_data = parser.dict_mgr.macro_dict[matched_macro]
            macro_type = macro_data.get("type", "default")

            if macro_type == "hotkey":
                return parser.builtins.execute_hotkey(macro_data)
            elif macro_type == "cmd":
                return parser._queue_command_macro_confirmation(
                    matched_macro,
                    macro_data,
                    raw_user_input_str,
                    session_id,
                )
            elif macro_type == "compound":
                return parser.builtins.execute_compound(macro_data, log_callback, image_data, mode)
            elif macro_type == "learned":
                # Local matching bypasses AI, but never bypasses the stored
                # user-owned run policy or the existing safety preflight.
                app_name = macro_data.get("app")
                learned_macro = getattr(parser.dict_mgr, "learned_macros", {}).get(
                    app_name, {}
                ).get(matched_macro)
                if not isinstance(learned_macro, dict):
                    return failure_result(
                        "사전 매칭된 학습 행동을 찾지 못했습니다.",
                        action="learned_macro", target=matched_macro,
                        error_type="target_not_found", data={"app": app_name},
                    )
                if learned_macro.get("state", "active") != "active":
                    return failure_result(
                        "이 학습 행동은 검토 또는 비활성 상태입니다. 행동 사전에서 상태를 확인해주세요.",
                        action="learned_macro", target=matched_macro,
                        error_type="validation_error", data={"app": app_name},
                    )
                slots = parser._build_slot_values(
                    learned_macro.get("learning", {}), template_match,
                    matched_app_name, matched_app_path,
                )
                target_arg = parser._build_learned_argument(
                    learned_macro, template_match, matched_app_name, matched_app_path,
                )
                policy_gate = parser._local_skill_policy_gate(
                    app_name=app_name,
                    macro_name=matched_macro,
                    skill=learned_macro,
                    slots=slots,
                    argument=target_arg,
                    session_id=session_id,
                    original_command=raw_user_input_str,
                )
                if policy_gate is not None:
                    return policy_gate
                try:
                    if log_callback: log_callback(f"[Execution] 사전 매칭된 학습 매크로({app_name}/{matched_macro}) 실행 중...")
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
                        original_command=raw_user_input_str,
                    )
                    if execution_result.get("status") == "confirmation_required":
                        return execution_result
                    verified = bool(execution_result.get("verified", False))
                    return success_result(
                        f"사전에 등록된 동의어로 [{matched_macro}] 매크로를 실행했습니다.",
                        action="learned_macro", target=matched_macro,
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
                        raw_user_input_str,
                    )
                except UIAutomationAmbiguousTarget as error:
                    return parser._queue_learned_uia_target_choice(
                        error,
                        app_name=app_name,
                        macro_name=matched_macro,
                        skill=learned_macro,
                        slots=slots,
                        session_id=session_id,
                        original_command=raw_user_input_str,
                        argument=target_arg,
                        source="local_match_confirmation",
                        target=learned_macro.get("default_target", ""),
                    )
                except ExecutionCancelled:
                    raise
                except Exception as e:
                    failure_type = parser._failure_type_for_error(e)
                    return failure_result(
                        f"사전 매칭된 매크로를 실행하다 에러가 났어 ㅠㅠ 에러: {e}",
                        action="learned_macro", target=matched_macro,
                        error_type=failure_type,
                        failed_step=getattr(e, "failed_step", None),
                        retryable=getattr(e, "retryable", False),
                        data={"app": app_name},
                        status=getattr(e, "status", "failed"),
                    )

            # Internal Default Macros
            handler = parser._handlers.get(matched_macro)
            if handler:
                return handler(user_input_str, normalized_tokens, matched_app_name, matched_app_path, log_callback)

        # Fallback to LLM if Basic Macro Matching Fails
        if routing_mode == "local_only":
            reason = analysis.get("reason", "등록된 로컬 명령을 찾지 못했습니다.")
            if analysis.get("kind") == "compound":
                failed_reasons = [
                    step.get("reason") for step in analysis.get("steps", [])
                    if not step.get("executable") and step.get("reason")
                ]
                if failed_reasons:
                    reason = failed_reasons[0]
            return failure_result(
                f"로컬 전용 모드라 AI를 호출하지 않았습니다. {reason}",
                action="command", error_type="validation_error",
            )
        if log_callback: log_callback("[Parser] 매크로 매칭 실패. AI 엔진에 의도 분석 요청 중...")
        result = parser.llm_engine.process_command(user_input_str, image_data, mode=mode, use_api=use_api, stream_callback=stream_callback)
        if isinstance(result, dict) and result.get("provider_error"):
            return failure_result(
                result.get("response", "AI 제공자 연결에 실패했습니다."),
                action="ai_provider", target=result.get("provider"),
                error_type="environment_error", retryable=True,
            )
        if isinstance(result, dict) and result.get("no_api_key"):
            return failure_result(
                result.get("response", "AI API 키가 없습니다."),
                action="ai_provider", target=result.get("provider"),
                error_type="validation_error",
            )
        return parser.ai_action_handler.handle_result(
            result,
            user_input_str,
            log_callback=log_callback,
            session_id=session_id,
            image_data=image_data,
            use_api=use_api,
        )
