def try_execute_native_route(
    parser,
    raw_user_input,
    *,
    session_id=None,
    log_callback=None,
):
    native_excel_request = (
        parser._parse_native_excel_write_command(raw_user_input)
        or parser._parse_native_excel_sum_command(raw_user_input)
        or parser._parse_native_excel_format_command(raw_user_input)
        or parser._parse_native_excel_range_format_command(raw_user_input)
        or parser._parse_native_excel_filter_command(raw_user_input)
        or parser._parse_native_excel_find_replace_command(raw_user_input)
        or parser._parse_native_excel_sort_command(raw_user_input)
        or parser._parse_native_excel_clarification_command(raw_user_input)
    )
    native_hwp_request = (
        parser._parse_native_hwp_find_replace_command(raw_user_input)
        or parser._parse_native_hwp_text_format_command(raw_user_input)
        or parser._parse_native_hwp_paragraph_format_command(raw_user_input)
        or parser._parse_native_hwp_insert_command(raw_user_input)
        or parser._parse_native_hwp_save_command(raw_user_input)
    )
    request = native_excel_request or native_hwp_request
    if not request:
        return None

    operation = request.get("operation")
    if operation == "choose_format_method":
        return parser._handle_format_method_request(
            request,
            session_id,
            raw_user_input,
            log_callback=log_callback,
        )
    if operation == "choose_hwp_replace_scope":
        return parser._queue_hwp_scope_choice(
            request,
            session_id,
            raw_user_input,
            log_callback=log_callback,
        )
    if operation == "request_missing_information":
        return parser._queue_missing_information(
            request,
            session_id,
            raw_user_input,
            log_callback=log_callback,
        )
    return parser._execute_native_app_command(
        request,
        session_id,
        raw_user_input,
        log_callback=log_callback,
    )
