from engine.pipeline.learned_route import execute_learned_route


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
        return parser._queue_command_macro_confirmation(
            matched_macro,
            macro_data,
            raw_user_input,
            session_id,
        )
    if macro_type == "compound":
        return parser.builtins.execute_compound(
            macro_data, log_callback, image_data, mode
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
