from engine.execution_result import failure_result


def resolve(owner, context):
    """Re-run a completed clarification sentence inside the resumed execution."""
    text = str(context.feedback_text or "").strip()
    if context.option_id != "provide_details" or not text:
        return failure_result(
            "추가 정보가 없어 작업을 실행하지 않았습니다.",
            action="clarification",
            error_type="validation_error",
            status="blocked",
        )
    target = str(context.payload.get("target") or "")
    expected = str(context.payload.get("context_identity") or "")
    try:
        current = owner.app_command_router.context_identity(target)
    except Exception as error:
        return owner._app_action_failure(error)
    if not expected or current != expected:
        return failure_result(
            "정보를 묻는 동안 활성 문서나 시트가 바뀌어 이전 요청을 실행하지 않았습니다. 현재 문서에서 다시 요청해주세요.",
            action="clarification",
            error_type="validation_error",
            status="context_changed",
        )
    return owner.execute_command_result(
        text,
        log_callback=context.log_callback,
        mode="command",
        use_api=False,
        session_id=context.session_id,
    )
