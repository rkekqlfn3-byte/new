"""Render truthful novice-facing copy from one structured event."""

from __future__ import annotations

from typing import Any, Mapping

from engine.user_feedback.event_models import UserFeedbackEvent
from engine.user_feedback.templates import ACTION_LABELS, ERROR_GUIDANCE


def _event_dict(value: UserFeedbackEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, UserFeedbackEvent):
        return value.to_dict()
    return dict(value or {})


def _action_label(event: Mapping[str, Any]) -> str:
    action = str(event.get("action") or "command")
    return ACTION_LABELS.get(action, "작업")


def render_user_event(
    value: UserFeedbackEvent | Mapping[str, Any],
) -> dict[str, Any]:
    """Add local display copy without copying raw result or technical text."""
    event = _event_dict(value)
    event_type = str(event.get("event_type") or "")
    label = _action_label(event)
    headline = "작업 상태를 확인하고 있어요."
    detail = "잠시만 기다려주세요."
    next_action = ""
    tone = "progress"

    if event_type == "request_received":
        headline = "요청을 확인하고 있어요."
        detail = "현재 문맥과 실행 가능 여부를 살펴보고 있습니다."
    elif event_type == "intent_resolved":
        headline = "할 일을 정리했어요."
        detail = f"{label} 대상과 안전 조건을 확인하고 있습니다."
    elif event_type in {"action_preparing", "action_started"}:
        headline = "작업을 준비하고 있어요." if event_type.endswith("preparing") else "작업을 시작했어요."
        detail = f"{label}을(를) 안전하게 처리하고 있습니다."
    elif event_type == "confirmation_required":
        headline = "실행 전에 확인이 필요해요."
        detail = "아래 내용을 확인하고 원하는 선택지를 골라주세요."
        next_action = "선택하기 전에는 컴퓨터 상태를 변경하지 않습니다."
        tone = "attention"
    elif event_type == "clarification_required":
        headline = "조금 더 알려주세요."
        detail = "대상이나 조건이 여러 가지라 임의로 선택하지 않았습니다."
        next_action = "표시된 선택지에서 원하는 대상을 골라주세요."
        tone = "attention"
    elif event_type in {"verification_passed", "action_completed"}:
        if bool(event.get("verified")):
            headline = "작업을 마치고 결과까지 확인했어요."
            detail = f"{label} 결과를 다시 읽어 요청대로 반영됐는지 확인했습니다."
            if bool(event.get("undo_available")):
                next_action = "필요하면 방금 작업을 되돌릴 수 있습니다."
            tone = "success"
        else:
            headline = "작업은 끝났지만 결과 확인이 필요해요."
            detail = "실행 완료와 결과 검증은 다르므로 확인 완료로 표시하지 않았습니다."
            next_action = "문서에서 결과를 확인해주세요."
            tone = "attention"
    elif event_type == "verification_failed":
        headline = "작업 결과가 예상과 달라 멈췄어요."
        detail = "잘못된 결과를 성공으로 표시하지 않았습니다."
        next_action = "문서 상태를 확인한 뒤 다시 요청해주세요."
        tone = "error"
    elif event_type == "action_failed":
        error_type = str((event.get("details") or {}).get("error_type") or "unknown")
        headline, next_action = ERROR_GUIDANCE.get(
            error_type, ERROR_GUIDANCE["unknown"]
        )
        detail = "실행을 중단했고 확인되지 않은 결과는 성공으로 기록하지 않았습니다."
        tone = "error"
    elif event_type == "action_cancelled":
        headline = "작업을 취소했어요."
        detail = "취소 뒤에는 새 요청을 기다립니다."
        tone = "neutral"
    elif event_type == "action_busy":
        headline, next_action = ERROR_GUIDANCE["busy"]
        detail = "동시에 두 작업을 실행해 대상이 섞이지 않도록 막았습니다."
        tone = "attention"
    elif event_type == "recovery_started":
        headline = "작업 대상을 한 번 다시 찾고 있어요."
        detail = "같은 대상임을 확인한 경우에만 원래 요청을 계속합니다."
    elif event_type == "recovery_completed":
        headline = "같은 작업 대상을 다시 찾았어요."
        detail = "대상이 바뀌지 않았는지 확인하고 원래 요청을 계속합니다."
        tone = "success"
    elif event_type == "recovery_failed":
        headline = "같은 작업 대상을 다시 찾지 못했어요."
        detail = "다른 대상을 임의로 선택하지 않고 멈췄습니다."
        next_action = "앱이나 문서를 확인한 뒤 다시 요청해주세요."
        tone = "error"
    elif event_type == "rollback_started":
        headline = "방금 작업을 되돌리고 있어요."
        detail = "원래 상태를 다시 읽어 복구 여부를 확인합니다."
    elif event_type == "rollback_completed":
        headline = "방금 작업을 되돌렸어요."
        detail = "원래 상태가 복구됐는지 다시 확인했습니다."
        tone = "success"
    elif event_type == "rollback_failed":
        headline = "작업을 안전하게 되돌리지 못했어요."
        detail = "확인되지 않은 복구를 성공으로 표시하지 않았습니다."
        next_action = "문서 상태를 직접 확인해주세요."
        tone = "error"

    rendered = dict(event)
    rendered["headline"] = headline
    rendered["detail"] = detail
    rendered["next_action"] = next_action
    rendered["tone"] = tone
    return rendered
