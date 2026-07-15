from engine.execution_result import failure_result


def resolve(owner, context):
    self = owner
    session_id = context.session_id
    consumed = context.consumed
    log_callback = context.log_callback
    payload = context.payload
    if payload.get("mode") == "local_learned":
        return self._resume_local_learned_dynamic(
            payload,
            consumed["confirmation_id"],
            log_callback=log_callback,
        )
    if payload.get("mode") == "ai_batch":
        return self._execute_ai_action_batch(
            payload.get("response", ""),
            payload.get("actions", []),
            payload.get("validation_issues", []),
            payload.get("user_input", consumed.get("original_command", "")),
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=payload.get(
                "approval_fingerprints", []
            ),
            approved_skill_runs=payload.get(
                "approved_skill_runs", []
            ),
            confirmation_id=consumed["confirmation_id"],
            image_data=payload.get("image_data"),
            use_api=bool(payload.get("use_api", False)),
        )
    return failure_result(
        "지원하지 않는 동적 코드 확인 후속 작업입니다.",
        action="dynamic_code",
        error_type="validation_error",
    )
