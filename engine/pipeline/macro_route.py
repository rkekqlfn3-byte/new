from engine.execution_result import failure_result, success_result
from engine.pipeline.learned_route import execute_learned_route


def execute_compound(parser, macro_data, log_callback, image_data, mode):
    commands = [item.strip() for item in macro_data["data"].split(",")]
    completed = []
    for index, command in enumerate(commands, start=1):
        result = parser.execute_command_result(
            command, log_callback, image_data, mode
        )
        completed.append(result)
        if not result["success"]:
            return failure_result(
                f"연속 동작 {index}단계에서 중단했습니다: {result['message']}",
                action="compound",
                error_type=result.get("error_type", "execution_error"),
                failed_step=index,
                retryable=result.get("retryable", False),
                data={"steps": completed},
            )
    return success_result(
        "연속 동작을 모두 수행했습니다.",
        action="compound",
        verified=all(item.get("verified") for item in completed),
        data={"steps": completed},
    )


def try_execute_macro_route(
    parser,
    *,
    matched_macro,
    normalized_tokens,
    matched_app_name,
    matched_app_path,
    template_match,
    raw_user_input,
    normalized_user_input,
    session_id=None,
    log_callback=None,
    image_data=None,
    mode="command",
):
    if not matched_macro:
        return None

    macro_data = parser.dict_mgr.macro_dict[matched_macro]
    macro_type = macro_data.get("type", "default")
    if macro_type == "hotkey":
        return parser.builtins.execute_hotkey(macro_data)
    if macro_type == "cmd":
        return parser.confirmations.queue_command_macro(
            parser,
            matched_macro,
            macro_data,
            raw_user_input,
            session_id,
        )
    if macro_type == "compound":
        return execute_compound(
            parser, macro_data, log_callback, image_data, mode
        )
    if macro_type == "learned":
        return execute_learned_route(
            parser,
            matched_macro=matched_macro,
            macro_data=macro_data,
            template_match=template_match,
            matched_app_name=matched_app_name,
            matched_app_path=matched_app_path,
            raw_user_input=raw_user_input,
            session_id=session_id,
            log_callback=log_callback,
        )

    handler = parser._handlers.get(matched_macro)
    if handler:
        return handler(
            normalized_user_input,
            normalized_tokens,
            matched_app_name,
            matched_app_path,
            log_callback,
        )
    return None
