from engine.edit_mode import ModePermissionError, RequestMode, normalize_request_mode
from engine.execution_result import failure_result
from engine.pipeline.ai_fallback_route import execute_ai_fallback_route
from engine.pipeline.conversation_route import execute_conversation_route
from engine.pipeline.edit_route import execute_edit_route
from engine.pipeline.macro_route import try_execute_macro_route
from engine.pipeline.native_route import try_execute_native_route
from engine.skills import strip_run_directive


_APPROVAL_WORDS = {
    "예", "네", "y", "yes", "응", "학습", "저장", "맞아", "그래", "ㅇㅇ", "ㅇ",
}
_REJECTION_WORDS = {
    "아니", "아니오", "아니요", "틀렸어", "취소", "버려", "ㄴㄴ", "ㄴ", "하지마",
}


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
        edit_context=None,
    ):
        parser = self.owner
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
        learning_review = _resolve_learning_review(parser, normalized_input)
        if learning_review is not None:
            return learning_review
        if normalized_input in {"확인 카드 테스트", "확인카드 테스트"}:
            return parser._queue_demo_confirmation(normalized_input, session_id)

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
            return macro_result

        return execute_ai_fallback_route(
            parser,
            user_input=normalized_input,
            analysis=analysis,
            routing_mode=routing_mode,
            log_callback=log_callback,
            image_data=image_data,
            mode=mode,
            use_api=use_api,
            stream_callback=stream_callback,
            session_id=session_id,
        )
