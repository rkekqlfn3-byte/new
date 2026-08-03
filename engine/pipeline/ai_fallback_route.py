from engine.execution_result import failure_result


def execute_ai_fallback_route(
    parser,
    *,
    user_input,
    analysis,
    routing_mode,
    log_callback=None,
    image_data=None,
    mode="command",
    use_api=False,
    summary="",
    conversation_state=None,
    stream_callback=None,
    session_id=None,
):
    if routing_mode == "local_only":
        reason = analysis.get("reason", "등록된 로컬 명령을 찾지 못했습니다.")
        if analysis.get("kind") == "compound":
            failed_reasons = [
                step.get("reason")
                for step in analysis.get("steps", [])
                if not step.get("executable") and step.get("reason")
            ]
            if failed_reasons:
                reason = failed_reasons[0]
        return failure_result(
            f"로컬 전용 모드라 AI를 호출하지 않았습니다. {reason}",
            action="command",
            error_type="validation_error",
        )

    if log_callback:
        log_callback("[Parser] 매크로 매칭 실패. AI 엔진에 의도 분석 요청 중...")
    result = parser.llm_engine.process_command(
        user_input,
        image_data,
        mode=mode,
        use_api=use_api,
        summary=summary,
        conversation_state=conversation_state,
        stream_callback=stream_callback,
    )
    if isinstance(result, dict) and result.get("provider_error"):
        return failure_result(
            result.get("response", "AI 제공자 연결에 실패했습니다."),
            action="ai_provider",
            target=result.get("provider"),
            error_type="environment_error",
            retryable=True,
        )
    if isinstance(result, dict) and result.get("no_api_key"):
        return failure_result(
            result.get("response", "AI API 키가 없습니다."),
            action="ai_provider",
            target=result.get("provider"),
            error_type="validation_error",
        )
    return parser.ai_action_handler.handle_result(
        parser,
        result,
        user_input,
        log_callback=log_callback,
        session_id=session_id,
        image_data=image_data,
        use_api=use_api,
    )
