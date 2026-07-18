import os

from engine.edit_mode import ActionScope, ModePermissionError, assert_action_allowed
from engine.execution_result import failure_result, success_result
from engine.security.launch_policy import UnsafeLaunchTarget, validate_launch_target


def execute_conversation_route(
    parser,
    user_input,
    *,
    log_callback=None,
    image_data=None,
    mode="conversation",
    use_api=False,
    summary="",
    stream_callback=None,
    conversation_state=None,
):
    latest_user_text = parser._latest_user_text(user_input)
    if parser._is_current_date_question(latest_user_text):
        if log_callback:
            log_callback("[Parser] 현재 날짜 질문을 로컬 시스템 시계로 처리합니다.")
        return parser.builtins.handle_date()
    if parser._is_current_time_question(latest_user_text):
        if log_callback:
            log_callback("[Parser] 현재 시간 질문을 로컬 시스템 시계로 처리합니다.")
        return parser.builtins.handle_time()
    if parser._is_current_weather_question(latest_user_text):
        if log_callback:
            log_callback("[Parser] 오늘 날씨 질문을 로컬 날씨 검색으로 처리합니다.")
        return parser.builtins.handle_weather(latest_user_text)

    if log_callback:
        history_length = len(user_input) if isinstance(user_input, list) else 1
        log_callback(
            f"[Parser] {mode} 모드(AI) 실행 중... "
            f"(history length: {history_length})"
        )

    result = parser.llm_engine.process_command(
        user_input,
        image_data,
        mode=mode,
        use_api=use_api,
        summary=summary,
        stream_callback=stream_callback,
        conversation_state=conversation_state,
    )
    if not isinstance(result, dict):
        result = {
            "response": str(result) if result else "AI가 빈 응답을 반환했습니다."
        }

    response = result.get("response", "으앙... 대답을 만들다 꼬였어 ㅠㅠ")
    if result.get("provider_error"):
        return failure_result(
            response,
            action="ai_provider",
            target=result.get("provider"),
            error_type="environment_error",
            retryable=True,
        )
    if result.get("no_api_key"):
        return failure_result(
            response,
            action="ai_provider",
            target=result.get("provider"),
            error_type="validation_error",
        )

    action = result.get("action", "none")
    target = result.get("target")
    if log_callback:
        log_callback(f"[LLM] Action: {action}, Target: {target}")

    if action == "open_app" and target:
        try:
            assert_action_allowed(mode, ActionScope.GLOBAL_ACTION)
        except ModePermissionError as error:
            return failure_result(
                str(error),
                action="mode_policy",
                target=target,
                error_type="validation_error",
                status="blocked",
            )
        path = parser.dict_mgr.noun_dict.get(target)
        if not path:
            return failure_result(
                f"등록된 앱을 찾지 못했습니다: {target}",
                action="open_app",
                target=target,
                error_type="target_not_found",
            )
        try:
            safe_path = validate_launch_target(path, target)
            if log_callback:
                log_callback(f"[Execution] {safe_path} 켜는 중...")
            os.startfile(safe_path)
            if not safe_path.casefold().startswith(("http://", "https://")):
                parser.action_executor.focus_window_when_ready(target)
        except UnsafeLaunchTarget as error:
            return failure_result(
                str(error),
                action="open_app",
                target=target,
                error_type="validation_error",
                retryable=False,
                status="blocked",
            )
        except Exception as error:
            if log_callback:
                log_callback(f"[Error] 실행 실패: {error}")
            return failure_result(
                response,
                action="open_app",
                target=target,
                error_type=parser._failure_type_for_error(error),
                retryable=False,
                data={"error": str(error)},
            )

    return success_result(response, action="conversation", verified=False)
