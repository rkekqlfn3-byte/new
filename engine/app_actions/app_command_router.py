"""Route prepared native app commands through one execution boundary.

The router deliberately sits above individual Excel/HWP adapters.  It keeps
adapter selection, preparation, decision handling, execution, and result
normalization out of CommandParser while reusing the existing registry and
confirmation services.
"""

from __future__ import annotations

import copy

from engine.app_actions.base import (
    AppActionAmbiguousTarget,
    AppActionContextChanged,
    AppActionError,
    PreparedAction,
)
from engine.execution_result import failure_result, normalize_error_type, success_result


class AppCommandRouter:
    """Common router for native application commands and prepared actions."""

    def __init__(self, owner):
        self.owner = owner

    @property
    def registry(self):
        # Tests and integrations may replace CommandParser's registry after
        # construction. Resolve it lazily so the router always uses the active
        # adapter set.
        return self.owner.app_action_registry

    @property
    def decision_engine(self):
        return self.owner.decision_engine

    @property
    def preference_manager(self):
        return self.owner.preference_manager

    def prepare(self, request_or_target, operation=None, params=None):
        """Select the adapter and prepare a COM-free action snapshot."""
        if isinstance(request_or_target, dict):
            request = request_or_target
            target = request.get("target")
            operation = request.get("operation")
            params = request.get("params", {})
        else:
            target = request_or_target
        return self.registry.prepare(target, operation, params or {})

    def execute_prepared(
        self,
        prepared,
        confirmation_id=None,
        preference_selection=None,
    ):
        """Execute one prepared action and convert it to the common result."""
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        execution_result = self.registry.execute(prepared)
        return self.build_success(
            prepared,
            execution_result,
            confirmation_id=confirmation_id,
            preference_selection=preference_selection,
        )

    def build_success(
        self,
        prepared,
        execution_result,
        confirmation_id=None,
        preference_selection=None,
    ):
        """Normalize adapter-specific success into one execution result."""
        changed = bool(execution_result.get("changed", False))
        if prepared.app == "hwp":
            if prepared.operation == "insert_text":
                message = (
                    f"한글 {prepared.workbook_name}의 {prepared.target}에 텍스트를 입력하고 확인했습니다."
                    if changed else "입력할 변경 사항이 없습니다."
                )
            elif prepared.operation == "set_text_format":
                message = (
                    "한글 선택 영역에 글자 서식을 적용하고 확인했습니다."
                    if changed else "선택 영역에 이미 같은 글자 서식이 적용되어 있습니다."
                )
            elif prepared.operation == "set_paragraph_format":
                message = (
                    f"한글 {prepared.target}의 문단 정렬을 적용하고 확인했습니다."
                    if changed else "문단이 이미 요청한 방식으로 정렬되어 있습니다."
                )
            elif prepared.operation == "find_replace":
                count = prepared.current_state.get("matching_count", 0)
                message = (
                    f"한글 {prepared.target}에서 {count}개 항목을 바꾸고 확인했습니다."
                    if changed else "바꿀 문자열을 찾지 못해 문서를 변경하지 않았습니다."
                )
            else:
                message = (
                    "한글 문서를 PDF로 저장하고 파일을 다시 열어 확인했습니다: "
                    f"{prepared.target}"
                )
        elif prepared.app not in {"excel", "hwp"}:
            message = (
                f"{self.app_label(prepared)} {prepared.target} 작업을 실행하고 확인했습니다."
                if changed else
                f"{self.app_label(prepared)} {prepared.target}은(는) 이미 요청한 상태입니다."
            )
        elif prepared.operation == "sum_column_to_cell":
            if changed:
                message = (
                    f"Excel {prepared.workbook_name}의 {prepared.sheet}!{prepared.target}에 "
                    f"{prepared.params.get('source_range')} 합계를 입력하고 다시 읽어 확인했습니다."
                )
            else:
                message = "합계 결과가 이미 같아 변경 없이 확인했습니다."
        elif prepared.operation == "apply_conditional_format":
            message = (
                f"Excel {prepared.sheet}!{prepared.target}에 조건부 서식을 적용하고 확인했습니다."
                if changed else "같은 조건부 서식이 이미 있어 변경하지 않았습니다."
            )
        elif prepared.operation == "format_matching_values":
            count = prepared.current_state.get("matching_count", 0)
            message = (
                f"Excel {prepared.sheet}!{prepared.target}에서 조건에 맞는 {count}개 셀을 표시하고 확인했습니다."
                if changed else "현재 조건에 맞는 셀이 없어 변경하지 않았습니다."
            )
        elif prepared.operation == "format_range":
            count = prepared.current_state.get("changing_count", 0)
            message = (
                f"Excel {prepared.sheet}!{prepared.target}의 {count}개 셀에 범위 서식을 적용하고 확인했습니다."
                if changed else "범위에 이미 같은 서식이 적용되어 있어 변경하지 않았습니다."
            )
        elif prepared.operation == "filter_range":
            message = (
                "Excel 필터를 해제하고 확인했습니다."
                if changed and prepared.params.get("clear") else
                f"Excel {prepared.sheet}!{prepared.target}에 필터를 적용하고 확인했습니다."
                if changed else "요청한 필터 상태가 이미 적용되어 있어 변경하지 않았습니다."
            )
        elif prepared.operation == "find_replace":
            count = prepared.current_state.get("matching_count", 0)
            message = (
                f"Excel {prepared.sheet}!{prepared.target}에서 {count}개 셀을 바꾸고 다시 읽어 확인했습니다."
                if changed else "바꿀 대상 문자열을 찾지 못해 변경하지 않았습니다."
            )
        elif prepared.operation == "sort_range":
            message = (
                f"Excel {prepared.sheet}!{prepared.target} 표의 모든 열을 함께 정렬하고 행 구성을 확인했습니다."
                if changed else "표가 이미 요청한 순서로 정렬되어 있어 변경하지 않았습니다."
            )
        elif changed:
            message = (
                f"Excel {prepared.workbook_name}의 {prepared.sheet}!{prepared.target}에 "
                "입력했고, 셀을 다시 읽어 결과를 확인했습니다."
            )
        else:
            message = (
                f"Excel {prepared.workbook_name}의 {prepared.sheet}!{prepared.target}은(는) "
                "이미 요청한 값이라 변경 없이 확인했습니다."
            )

        data = {
            "prepared_action": prepared.to_dict(),
            "execution_result": execution_result,
        }
        if isinstance(preference_selection, dict):
            preference_key = preference_selection.get("key")
            preference_method = preference_selection.get("method")
            if preference_key and preference_method:
                learned = self.preference_manager.record_selection(
                    preference_key,
                    preference_method,
                    remember=bool(preference_selection.get("remember", False)),
                )
                data["preference_learning"] = {
                    "key": preference_key,
                    "preferred_method": learned.get("preferred_method"),
                    "selection_count": learned.get("selection_count", 0),
                    "confidence": learned.get("confidence", 0.0),
                    "locked": learned.get("locked", False),
                }
                if preference_selection.get("remember"):
                    message += " 앞으로 같은 요청에는 이 방식을 우선 사용합니다."
        if confirmation_id:
            data["confirmation_id"] = confirmation_id
        return success_result(
            message,
            action="app_command",
            target=f"{prepared.workbook_name}/{prepared.sheet}/{prepared.target}",
            verified=True,
            verification_status="verified",
            data=data,
        )

    @staticmethod
    def failure(error, target=None):
        return failure_result(
            str(error),
            action="app_command",
            target=target,
            error_type=normalize_error_type(
                getattr(error, "error_type", "execution_error"),
                default="execution_error",
            ),
            retryable=False,
            status=getattr(error, "status", "failed"),
        )

    @staticmethod
    def app_label(prepared):
        labels = {"excel": "Excel", "hwp": "한글", "word": "Word", "ppt": "PowerPoint"}
        return labels.get(prepared.app, prepared.app or "앱")

    def execute(self, request, session_id, original_command, log_callback=None):
        """Prepare, decide, execute, and normalize one native app request."""
        prepared = None
        try:
            prepared = self.prepare(request)
            decision = self.decision_engine.evaluate(prepared)
            if decision.decision == "blocked":
                return failure_result(
                    decision.message or "안전하게 실행할 수 없는 앱 작업입니다.",
                    action="app_command",
                    error_type="validation_error",
                    status="blocked",
                )
            if decision.requires_confirmation:
                if log_callback:
                    log_callback(
                        f"[Confirmation] {self.app_label(prepared)} "
                        f"{prepared.sheet}!{prepared.target} 변경 확인 필요"
                    )
                return self.owner._queue_prepared_action_confirmation(
                    prepared, request, decision, session_id, original_command
                )
            if log_callback:
                log_callback(
                    f"[AppAction] {self.app_label(prepared)} "
                    f"{prepared.sheet}!{prepared.target} 실행 및 검증"
                )
            result = self.execute_prepared(
                prepared,
                preference_selection=request.get("_preference_selection"),
            )
            auto_preference = request.get("_auto_preference")
            if isinstance(auto_preference, dict):
                learned = self.preference_manager.record_success(
                    auto_preference.get("key"), auto_preference.get("method")
                )
                result.setdefault("data", {})["preference_auto_applied"] = {
                    "key": auto_preference.get("key"),
                    "method": auto_preference.get("method"),
                    "confidence": (
                        learned.get("confidence", 0.0)
                        if isinstance(learned, dict) else 0.0
                    ),
                }
            return result
        except AppActionAmbiguousTarget as error:
            return self.owner._queue_app_target_choice(
                request, error, session_id, original_command
            )
        except AppActionContextChanged:
            return self.owner._queue_changed_app_context(
                request, prepared, session_id, original_command
            )
        except AppActionError as error:
            auto_preference = request.get("_auto_preference")
            if (
                isinstance(auto_preference, dict)
                and getattr(error, "error_type", "") == "verification_error"
            ):
                failure = self.preference_manager.record_failure(
                    auto_preference.get("key"), auto_preference.get("method")
                )
                if failure.get("disabled"):
                    base_request = copy.deepcopy(
                        auto_preference.get("base_request") or request
                    )
                    base_request.pop("_auto_preference", None)
                    base_request["operation"] = "choose_format_method"
                    return self.owner._queue_app_method_choice(
                        base_request,
                        session_id,
                        original_command,
                        log_callback=log_callback,
                    )
            return self.failure(error)

    def queue_changed_context(
        self,
        request,
        previous,
        session_id,
        original_command,
        execution_id="",
        preference_selection=None,
    ):
        """Re-prepare a changed document and require a fresh confirmation."""
        try:
            current = self.prepare(request)
            decision = self.decision_engine.evaluate(
                current, force_confirmation=True
            )
            return self.owner._queue_prepared_action_confirmation(
                current,
                request,
                decision,
                session_id,
                original_command,
                execution_id=execution_id,
                preference_selection=preference_selection,
            )
        except AppActionError as error:
            target = None
            if isinstance(previous, PreparedAction):
                target = (
                    f"{previous.workbook_name}/{previous.sheet}/{previous.target}"
                )
            return self.failure(error, target)
