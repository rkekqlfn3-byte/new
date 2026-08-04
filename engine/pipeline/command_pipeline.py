from engine.edit_mode import ModePermissionError, RequestMode, normalize_request_mode
from engine.execution_result import failure_result
from engine.managers.app_scanner import matches_requested_app_candidate
from engine.pipeline.ai_fallback_route import execute_ai_fallback_route
from engine.pipeline.conversation_route import execute_conversation_route
from engine.pipeline.edit_route import execute_edit_route
from engine.pipeline.macro_route import try_execute_macro_route
from engine.pipeline.native_route import try_execute_native_route
from engine.pipeline.pdf_route import try_execute_pdf_route
from engine.recovery import (
    PreExecutionRecoveryContract,
    recovery_target_signature,
)
from engine.security.launch_policy import looks_like_shell_execution_request
from engine.skills import strip_run_directive

_APPROVAL_WORDS = {
    "예", "네", "y", "yes", "응", "학습", "저장", "맞아", "그래", "ㅇㅇ", "ㅇ",
}
_REJECTION_WORDS = {
    "아니", "아니오", "아니요", "틀렸어", "취소", "버려", "ㄴㄴ", "ㄴ", "하지마",
}
_SELF_DIAGNOSIS_COMMANDS = frozenset({
    "자가 진단", "자가진단", "최근 오류 진단", "최근 오류 진단해줘",
    "최근 실패 진단", "최근 실패 원인", "방금 오류 왜 실패했어",
})


def _normalize_command_input(user_input):
    if isinstance(user_input, list):
        user_input = user_input[-1].get("content", "") if user_input else ""
    raw = str(user_input or "").strip()
    return raw, raw.lower()


def _resolve_learning_review(parser, normalized_input):
    if not getattr(parser, "pending_macros", []):
        return None
    if normalized_input in _APPROVAL_WORDS:
        return parser.approve_pending_learning()
    if normalized_input in _REJECTION_WORDS:
        return parser.reject_pending_learning()
    return failure_result(
        "학습 검토가 대기 중입니다. 검토 화면에서 저장·이번만 실행·폐기 중 하나를 선택해주세요.",
        action="learning_review",
        error_type="validation_error",
        status="confirmation_required",
    )


def _routing_mode(parser):
    mode = parser.dict_mgr.get_ai_config().get("routing_mode", "auto")
    return mode if mode in {"auto", "local_only", "ai_first"} else "auto"


def _attach_recovery_metadata(result, recovery):
    if not isinstance(result, dict) or not isinstance(recovery, dict):
        return result
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    data = dict(data)
    data["automatic_recovery"] = dict(recovery)
    data["retry_count"] = int(recovery.get("retry_count", 0) or 0)
    result["data"] = data

    if recovery.get("outcome") == "recovered":
        note = "앱 목록을 다시 확인해 실행 대상을 찾았습니다."
    elif recovery.get("outcome") == "not_found":
        note = "Windows 앱 목록도 다시 확인했지만 찾지 못했습니다. 앱 이름이나 위치를 알려주세요."
    elif recovery.get("outcome") == "target_changed":
        note = "재탐색 전후의 요청 대상이 달라 자동 실행을 멈췄습니다. 정확한 앱 이름이나 위치를 알려주세요."
        hints = data.get("triage_hints") if isinstance(data.get("triage_hints"), dict) else {}
        hints = dict(hints)
        hints["recovery_target_changed"] = True
        data["triage_hints"] = hints
    elif recovery.get("outcome") == "blocked_after_execution":
        note = "실행이 시작된 뒤에는 중복 변경 위험 때문에 자동 재시도하지 않았습니다."
        hints = data.get("triage_hints") if isinstance(data.get("triage_hints"), dict) else {}
        hints = dict(hints)
        hints["recovery_after_execution_blocked"] = True
        data["triage_hints"] = hints
    else:
        note = "Windows 앱 목록을 다시 확인하는 중 문제가 발생했습니다."
        hints = data.get("triage_hints") if isinstance(data.get("triage_hints"), dict) else {}
        hints = dict(hints)
        hints["environment_blocked"] = True
        data["triage_hints"] = hints

    message = str(result.get("message", result.get("response", "")) or "").rstrip()
    if note and note not in message:
        message = f"{message}\n{note}" if message else note
        result["message"] = message
        result["response"] = message
    return result


def _recover_missing_app_target(parser, raw_input, analysis, log_callback=None):
    """Perform one pre-execution, bounded target rediscovery attempt."""
    if (
        not isinstance(analysis, dict)
        or analysis.get("kind") != "single"
        or analysis.get("macro") not in {"OPEN", "CLOSE"}
        or analysis.get("app_path")
    ):
        return analysis, None
    discover = getattr(parser.dict_mgr, "discover_apps", None)
    if not callable(discover):
        return analysis, None
    candidates = parser.local_command_analyzer.build_app_candidates(
        analysis.get("tokens", [])
    )
    if not candidates:
        return analysis, None

    signature = recovery_target_signature(analysis.get("macro"), candidates)
    contract = PreExecutionRecoveryContract(
        strategy="bounded_app_rediscovery",
        target_kind="app",
        target_signature=signature,
    )
    if not contract.begin(signature):
        return analysis, contract.to_dict()
    parser.execution_controller.event(
        "automatic_recovery",
        "retrying",
        {
            "route": "bounded_app_rediscovery",
            "phase": "pre_execution",
            "execution_started": False,
            "target_unchanged": True,
            "retry_count": contract.retry_count,
            "retry_limit": contract.retry_limit,
        },
    )
    if log_callback:
        log_callback("[Recovery] 등록된 앱 대상을 제한적으로 다시 찾는 중입니다.")
    try:
        discover(candidates)
        refreshed = parser.analyze_command(raw_input)
        refreshed_candidates = parser.local_command_analyzer.build_app_candidates(
            refreshed.get("tokens", []) if isinstance(refreshed, dict) else []
        )
        refreshed_signature = recovery_target_signature(
            refreshed.get("macro") if isinstance(refreshed, dict) else None,
            refreshed_candidates,
        )
        resolved_match = bool(
            isinstance(refreshed, dict)
            and refreshed.get("kind") == "single"
            and refreshed.get("macro") == analysis.get("macro")
            and refreshed.get("app_name")
            and refreshed.get("app_path")
            and matches_requested_app_candidate(
                refreshed.get("app_name"),
                refreshed.get("app_path"),
                candidates,
            )
        )
        outcome = contract.complete(
            "recovered" if resolved_match else "not_found",
            current_target_signature=refreshed_signature,
            target_resolved=resolved_match,
        )
        recovery = contract.to_dict()
        parser.execution_controller.event(
            "automatic_recovery",
            outcome,
            {
                "route": "bounded_app_rediscovery",
                "phase": "pre_execution",
                "execution_started": recovery["execution_started"],
                "target_unchanged": recovery["target_unchanged"],
                "retry_count": recovery["retry_count"],
                "retry_limit": recovery["retry_limit"],
                "outcome": outcome,
            },
        )
        return (refreshed if outcome == "recovered" else analysis), recovery
    except Exception:
        contract.complete(
            "unavailable",
            current_target_signature=signature,
            target_resolved=False,
        )
        recovery = contract.to_dict()
        parser.execution_controller.event(
            "automatic_recovery",
            "failed",
            {
                "route": "bounded_app_rediscovery",
                "phase": "pre_execution",
                "execution_started": recovery["execution_started"],
                "target_unchanged": recovery["target_unchanged"],
                "retry_count": recovery["retry_count"],
                "retry_limit": recovery["retry_limit"],
                "outcome": recovery["outcome"],
                "error_type": "environment_error",
            },
        )
        return analysis, recovery


class CommandPipeline:
    """Route one command through local, native-app, learned, and AI stages."""

    def execute(
        self,
        parser,
        user_input,
        log_callback=None,
        image_data=None,
        mode="command",
        use_api=False,
        summary="",
        stream_callback=None,
        conversation_state=None,
        session_id=None,
        edit_context=None,
    ):
        parser.action_executor.noun_dict = parser.dict_mgr.noun_dict
        try:
            mode = normalize_request_mode(mode).value
        except ModePermissionError as error:
            return failure_result(
                str(error),
                action="mode_policy",
                error_type="validation_error",
                status="blocked",
            )
        if mode == RequestMode.EDIT.value:
            return execute_edit_route(
                parser,
                user_input,
                edit_context=edit_context,
                session_id=session_id,
                log_callback=log_callback,
            )
        if mode in {RequestMode.CONVERSATION.value, RequestMode.QUESTION.value}:
            return execute_conversation_route(
                parser,
                user_input,
                log_callback=log_callback,
                image_data=image_data,
                mode=mode,
                use_api=use_api,
                summary=summary,
                stream_callback=stream_callback,
                conversation_state=conversation_state,
            )

        raw_input, normalized_input = _normalize_command_input(user_input)
        if parser._is_current_date_question(raw_input):
            if log_callback:
                log_callback("[Parser] 현재 날짜 질문을 로컬 시스템 시계로 처리합니다.")
            return parser.builtins.handle_date()
        if parser._is_current_time_question(raw_input):
            if log_callback:
                log_callback("[Parser] 현재 시간 질문을 로컬 시스템 시계로 처리합니다.")
            return parser.builtins.handle_time()
        if parser._is_current_weather_question(raw_input):
            if log_callback:
                log_callback("[Parser] 오늘 날씨 질문을 로컬 날씨 검색으로 처리합니다.")
            return parser.builtins.handle_weather(raw_input)
        if looks_like_shell_execution_request(raw_input):
            return failure_result(
                "명령 프롬프트·PowerShell·터미널을 통한 직접 명령 실행은 보안상 허용하지 않습니다.",
                action="command_line",
                error_type="validation_error",
                status="blocked",
            )
        if normalized_input in _SELF_DIAGNOSIS_COMMANDS:
            return parser.diagnose_latest_failure()
        learning_review = _resolve_learning_review(parser, normalized_input)
        if learning_review is not None:
            return learning_review
        if normalized_input in {"확인 카드 테스트", "확인카드 테스트"}:
            return parser.confirmations.queue_demo(normalized_input, session_id)

        pdf_result = try_execute_pdf_route(
            parser,
            raw_input,
            session_id=session_id,
            log_callback=log_callback,
        )
        if pdf_result is not None:
            return pdf_result

        native_result = try_execute_native_route(
            parser,
            raw_input,
            session_id=session_id,
            log_callback=log_callback,
        )
        if native_result is not None:
            return native_result

        routing_mode = _routing_mode(parser)
        analysis = parser.analyze_command(strip_run_directive(normalized_input))
        recovery = None
        if routing_mode != "ai_first":
            analysis, recovery = _recover_missing_app_target(
                parser,
                raw_input,
                analysis,
                log_callback=log_callback,
            )
        force_ai = analysis["kind"] == "compound" or routing_mode == "ai_first"
        if analysis["kind"] == "compound" and force_ai:
            compound_parts = [step["text"] for step in analysis["steps"]]
            if analysis["executable"] and routing_mode != "ai_first":
                return parser._execute_local_compound(
                    compound_parts, log_callback, image_data, use_api
                )
            if log_callback:
                log_callback(
                    "[Parser] 복합 명령 일부를 로컬에서 해석할 수 없어 "
                    "전체 문장을 AI로 전달합니다."
                )
        elif force_ai and log_callback:
            log_callback("[Router] AI 우선 모드로 명령 전체를 AI에 전달합니다.")

        matched_macro = None if force_ai else analysis.get("macro")
        matched_app_name = analysis.get("app_name")
        if log_callback:
            log_callback(
                f"[Parser] 매크로: {matched_macro}, 앱: {matched_app_name}"
            )

        macro_result = try_execute_macro_route(
            parser,
            matched_macro=matched_macro,
            normalized_tokens=analysis.get(
                "tokens", parser.normalize_text(normalized_input)
            ),
            matched_app_name=matched_app_name,
            matched_app_path=analysis.get("app_path"),
            template_match=analysis.get("template_match"),
            raw_user_input=raw_input,
            normalized_user_input=normalized_input,
            session_id=session_id,
            log_callback=log_callback,
            image_data=image_data,
            mode=mode,
        )
        if macro_result is not None:
            return _attach_recovery_metadata(macro_result, recovery)

        return execute_ai_fallback_route(
            parser,
            user_input=normalized_input,
            analysis=analysis,
            routing_mode=routing_mode,
            log_callback=log_callback,
            image_data=image_data,
            mode=mode,
            use_api=use_api,
            summary=summary,
            conversation_state=conversation_state,
            stream_callback=stream_callback,
            session_id=session_id,
        )
