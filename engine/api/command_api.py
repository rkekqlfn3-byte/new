import logging

import eel
from engine.core import get_parser
from engine.execution_runtime import ExecutionBusyError, ExecutionCancelled
from engine.execution_result import failure_result
from engine.managers.pending_confirmation_manager import normalize_session_id


logger = logging.getLogger(__name__)

# Optional compatibility injection point for isolated API tests/integrations.
# Production startup leaves this unset and resolves the explicit core service.
parser = None


def _get_parser():
    return parser if parser is not None else get_parser()


_NON_OWNING_CONFIRMATION_STATUSES = frozenset({
    "confirmation_not_found",
    "confirmation_conflict",
    "confirmation_expired",
    "confirmation_already_consumed",
    "confirmation_session_mismatch",
    "confirmation_invalid_option",
    "confirmation_error",
    "state_conflict",
})


def _log_to_terminal(msg):
    logger.info("%s", msg)
    try:
        eel.log_terminal(msg)()
    except Exception:
        logger.debug("Could not forward terminal log to Eel", exc_info=True)


def _stream_callback(chunk):
    try:
        eel.receive_stream_chunk(chunk)()
    except Exception:
        logger.debug("Could not forward stream chunk to Eel", exc_info=True)


def _finish_or_pause(result, execution_id=None):
    parser = _get_parser()
    confirmation = (
        result.get("data", {}).get("confirmation", {})
        if isinstance(result, dict) and isinstance(result.get("data"), dict)
        else {}
    )
    if (
        result.get("status") == "confirmation_required"
        and confirmation.get("confirmation_id")
    ):
        parser.execution_controller.pause_for_confirmation(
            confirmation["confirmation_id"],
            response=result.get("message", ""),
            extra={
                "session_id": confirmation.get("session_id", "default"),
                "result": result,
            },
            expected_execution_id=execution_id,
        )
        return result

    # These results describe only the submitted confirmation response. They do
    # not own the paused/resumed command and must never finish its runtime slot.
    if result.get("status") in _NON_OWNING_CONFIRMATION_STATUSES:
        return result

    is_non_failure_confirmation = result.get("status") == "confirmation_required"
    parser.execution_controller.finish(
        result["success"], result.get("status"),
        response=result.get("message", ""),
        error=(
            "" if result["success"] or is_non_failure_confirmation
            else result.get("message", "")
        ),
        extra={
            "execution_id": execution_id,
            "verified": result.get("verified", False),
            "error_type": result.get("error_type"),
            "failed_step": result.get("failed_step"),
            "retryable": result.get("retryable", False),
            "result": result,
        },
        expected_execution_id=execution_id,
    )
    return result


def _resolve_confirmation_request(
    session_id,
    confirmation_id=None,
    option_id=None,
    user_text=None,
    remember_preference=False,
):
    parser = _get_parser()
    record = (
        parser.pending_confirmation_manager.get_record(confirmation_id)
        if confirmation_id else
        parser.pending_confirmation_manager.active_record(session_id)
    )
    execution_id = record.get("execution_id", "") if record else ""
    try:
        result = parser.resolve_pending_confirmation(
            session_id,
            confirmation_id=confirmation_id,
            option_id=option_id,
            user_text=user_text,
            log_callback=_log_to_terminal,
            remember_preference=remember_preference,
        )
        return _finish_or_pause(result, execution_id)
    except ExecutionCancelled as error:
        parser.execution_controller.finish(
            False, "cancelled", response="실행을 취소했습니다.", error=str(error),
            extra={
                "execution_id": execution_id,
                "verified": False,
                "error_type": "user_cancelled",
                "failed_step": None,
                "retryable": False,
            },
            expected_execution_id=execution_id,
        )
        return failure_result(
            "현재 실행을 취소했습니다.", action="confirmation",
            error_type="user_cancelled",
        )
    except Exception as error:
        logger.exception(
            "Confirmation response handling failed",
            extra={
                "execution_id": execution_id or "-",
                "session_id": session_id,
                "route": "confirmation",
                "error_type": parser._failure_type_for_error(error),
            },
        )
        parser.execution_controller.finish(
            False, "failed", error=str(error), extra={
                "execution_id": execution_id,
                "verified": False,
                "error_type": parser._failure_type_for_error(error),
                "failed_step": getattr(error, "failed_step", None),
                "retryable": getattr(error, "retryable", False),
            },
            expected_execution_id=execution_id,
        )
        return failure_result(
            "확인 응답 처리 중 오류가 발생했습니다. 로그를 확인해주세요.",
            action="confirmation",
            error_type=parser._failure_type_for_error(error),
        )

@eel.expose
def parse_command(
    user_input,
    image_data=None,
    mode="command",
    use_api=False,
    summary="",
    conversation_state=None,
    session_id=None,
    edit_context=None,
):
    parser = _get_parser()
    logger.info("Received command mode=%s api=%s", mode, bool(use_api))
    session_id = normalize_session_id(session_id)
    if mode == "command" and parser.get_pending_confirmation(session_id):
        return _resolve_confirmation_request(
            session_id, user_text=str(user_input or "")
        )

    try:
        execution_id = parser.execution_controller.begin(
            str(user_input)[-200:], {
                "mode": mode, "use_api": bool(use_api), "session_id": session_id,
            }
        )
    except ExecutionBusyError as error:
        # Another command is still running; do not touch its diagnostics.
        return failure_result(
            str(error), action="command",
            error_type="busy", retryable=True, status="busy",
        )
    try:
        result = parser.execute_command_result(
            user_input,
            log_callback=_log_to_terminal,
            image_data=image_data,
            mode=mode,
            use_api=use_api,
            summary=summary,
            stream_callback=_stream_callback,
            conversation_state=conversation_state,
            session_id=session_id,
            edit_context=edit_context,
        )
        return _finish_or_pause(result, execution_id)
    except ExecutionCancelled as error:
        _log_to_terminal("[Execution] 사용자가 현재 실행을 취소했습니다.")
        parser.execution_controller.finish(
            False, "cancelled", response="실행을 취소했습니다.", error=str(error),
            extra={
                "execution_id": execution_id,
                "verified": False,
                "error_type": "user_cancelled",
                "failed_step": None,
                "retryable": False,
            },
            expected_execution_id=execution_id,
        )
        return failure_result(
            "현재 실행을 취소했습니다.", action="command",
            error_type="user_cancelled",
        )
    except Exception as e:
        logger.exception(
            "Command handling failed",
            extra={
                "execution_id": execution_id,
                "session_id": session_id,
                "route": "command",
                "error_type": parser._failure_type_for_error(e),
            },
        )
        _log_to_terminal("[Error] 대화 처리에 실패했습니다. 로그를 확인해주세요.")
        parser.execution_controller.finish(
            False, "failed", error=str(e), extra={
                "execution_id": execution_id,
                "verified": False,
                "error_type": parser._failure_type_for_error(e),
                "failed_step": getattr(e, "failed_step", None),
                "retryable": getattr(e, "retryable", False),
            },
            expected_execution_id=execution_id,
        )
        return failure_result(
            "대화 처리 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.",
            action="command", error_type=parser._failure_type_for_error(e),
        )


@eel.expose
def get_pending_confirmation(session_id=None):
    parser = _get_parser()
    confirmation = parser.get_pending_confirmation(session_id)
    return {"pending": bool(confirmation), "confirmation": confirmation}


@eel.expose
def resolve_confirmation(
    confirmation_id, option_id, session_id=None, remember_preference=False
):
    return _resolve_confirmation_request(
        normalize_session_id(session_id),
        confirmation_id=confirmation_id,
        option_id=option_id,
        remember_preference=remember_preference,
    )


@eel.expose
def cancel_current_execution():
    parser = _get_parser()
    return {"success": parser.cancel_current_execution()}


@eel.expose
def get_execution_diagnostics(limit=20):
    parser = _get_parser()
    return parser.get_execution_diagnostics(limit)


@eel.expose
def get_native_action_candidates(include_observing=True, include_dismissed=False):
    parser = _get_parser()
    return {
        "success": True,
        "candidates": parser.get_native_action_candidates(
            include_observing=include_observing,
            include_dismissed=include_dismissed,
        ),
    }


@eel.expose
def set_native_action_candidate_status(candidate_id, status, reason=""):
    parser = _get_parser()
    try:
        return {
            "success": True,
            "candidate": parser.set_native_action_candidate_status(
                candidate_id, status, reason=reason
            ),
        }
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}


@eel.expose
def get_native_action_candidate_spec(candidate_id):
    parser = _get_parser()
    try:
        return {
            "success": True,
            "spec": parser.get_native_action_candidate_spec(candidate_id),
        }
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}


@eel.expose
def retry_learned_macro_step(app_name, macro_name, step_number):
    parser = _get_parser()
    try:
        result = parser.retry_learned_macro_step(app_name, macro_name, step_number)
        return {"success": True, "result": result}
    except (TypeError, ValueError, RuntimeError) as error:
        return failure_result(
            str(error), action="retry_learned_macro",
            target=macro_name, error_type=parser._failure_type_for_error(error),
            failed_step=getattr(error, "failed_step", step_number),
            retryable=getattr(error, "retryable", False),
            data={"app": app_name},
            status=getattr(error, "status", "failed"),
        )


@eel.expose
def get_pending_learning_review():
    parser = _get_parser()
    return parser.get_pending_learning_review()


@eel.expose
def approve_pending_learning(edits=None):
    parser = _get_parser()
    try:
        message = parser.approve_pending_learning(edits)
        return {"success": True, "message": message}
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}


@eel.expose
def reject_pending_learning(reason="discard"):
    parser = _get_parser()
    return {
        "success": True,
        "message": parser.reject_pending_learning(reason),
    }

@eel.expose
def summarize_memory(current_summary, old_messages):
    import json
    parser = _get_parser()
    dict_mgr = parser.dict_mgr
    if not old_messages:
        return {"success": True, "summary": current_summary}
        
    config = dict_mgr.get_ai_config()
    provider = config.get("provider", "openai")
    api_key = config.get("api_key", "")
    if not api_key:
        return {"success": False, "summary": current_summary}
    
    prompt = (
        "You are an AI memory summarizer. Your task is to seamlessly compress the provided chat history into a concise summary.\n"
        "If there is an existing summary, you must integrate the new messages into it.\n"
        "IMPORTANT: The summary MUST be under 800 Korean characters.\n"
        "Respond strictly in Korean. Output only the summary text with no headings or labels.\n\n"
        f"Existing Summary:\n{current_summary}\n\n"
        f"New Messages to Integrate:\n{json.dumps(old_messages, ensure_ascii=False)}\n\n"
        "Updated Compressed Summary (under 800 chars):"
    )
    
    try:
        gen_temp = 0.3
        def run_llm(p):
            if provider == "openai" and api_key:
                return parser.llm_engine._call_openai(api_key, p, "Summarize Memory", mode="question", temperature=gen_temp)
            elif provider == "gemini" and api_key:
                return parser.llm_engine._call_gemini(api_key, p, "Summarize Memory", mode="question", temperature=gen_temp)
            raise ValueError("지원하지 않는 AI 제공자입니다.")

        res = run_llm(prompt)
        summary_text = res.get("response", current_summary).strip()
        
        if len(summary_text) > 800:
            recompress_prompt = f"다음 요약문이 800자를 초과했습니다. 핵심 내용만 남기고 800자 이내로 다시 압축하세요.\n\n원본:\n{summary_text}\n\n800자 이내 재압축:"
            res2 = run_llm(recompress_prompt)
            summary_text = res2.get("response", summary_text).strip()
            if len(summary_text) > 800:
                summary_text = summary_text[len(summary_text) - 800:]
            
        return {
            "success": True,
            "summary": summary_text
        }
    except Exception:
        logger.exception("대화 메모리 요약 실패")
        return {"success": False, "summary": current_summary}
