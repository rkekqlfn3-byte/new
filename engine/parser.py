import os
import copy
import json
import subprocess  # Kept as a compatibility patch point for older integrations.
import urllib.parse
import ctypes
import re
import time
from datetime import datetime
from engine.managers.dict_manager import DictionaryManager
from engine.llm_engine import LLMEngine
from engine.template_matcher import LearnedTemplateMatcher
from engine.action_executor import (
    ActionExecutor, ActionPlanError,
    ActionPlanVerificationError,
)
from engine.execution_runtime import ExecutionCancelled, ExecutionController
from engine.execution_result import (
    failure_result,
    normalize_error_type,
    normalize_execution_result,
    success_result,
)
from engine.macro_runner import MacroRunner, MacroTimeoutError
from engine.managers.pending_confirmation_manager import (
    PendingConfirmationManager,
    normalize_session_id,
)
from engine.confirmation import ConfirmationDispatcher, ConfirmationResponseHandler
from engine.ai_actions import AIActionHandler
from engine.skills import (
    CandidateRecordingService,
    DIRECTIVE_PREVIEW,
    SkillConfirmationRequired,
    SkillContextChanged,
    SkillExecutor,
    SkillLearningService,
    SkillPreflightBlocked,
    SkillRunPolicyService,
    skill_policy_fingerprint,
)
from engine.local_commands import LocalCommandAnalyzer
from engine.pipeline import CommandPipeline
from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)
from engine.security import BLOCKED, CONFIRMATION_REQUIRED, DynamicCodePreflight
from engine.app_actions import (
    AppActionAmbiguousTarget,
    AppCommandRouter,
    AppActionError,
    AppActionRegistry,
    PreparedAction,
)
from engine.decision import DecisionEngine, PreferenceManager
import psutil
import win32gui
import win32process
import win32con
from engine.builtins import BuiltinMacros
from engine.parsing.office_command_parser import (
    EXCEL_FORMAT_METHODS,
    EXCEL_FORMAT_PREFERENCE_KEY,
    parse_native_excel_filter_command,
    parse_native_excel_find_replace_command,
    parse_native_excel_format_command,
    parse_native_excel_range_format_command,
    parse_native_excel_sort_command,
    parse_native_excel_sum_command,
    parse_native_excel_write_command,
    parse_native_hwp_find_replace_command,
    parse_native_hwp_insert_command,
    parse_native_hwp_paragraph_format_command,
    parse_native_hwp_save_command,
    parse_native_hwp_text_format_command,
)

class CommandParser:
    def __init__(self, native_action_candidate_manager=None):
        self.dict_mgr = DictionaryManager()
        self.llm_engine = LLMEngine(self.dict_mgr)
        
        self.pending_macros = []
        self.template_matcher = LearnedTemplateMatcher()
        self.local_command_analyzer = LocalCommandAnalyzer(self)
        self.command_pipeline = CommandPipeline(self)
        self.execution_controller = ExecutionController()
        self.pending_confirmation_manager = PendingConfirmationManager()
        self.confirmation_response_handler = ConfirmationResponseHandler(self)
        self.confirmation_dispatcher = ConfirmationDispatcher(self)
        self.ai_action_handler = AIActionHandler(self)
        self.app_action_registry = AppActionRegistry()
        self.decision_engine = DecisionEngine()
        self.preference_manager = PreferenceManager()
        self.app_command_router = AppCommandRouter(self)
        self._native_candidate_manager_injected = (
            native_action_candidate_manager is not None
        )
        self.native_action_candidate_manager = (
            native_action_candidate_manager or NativeActionCandidateManager()
        )
        self.candidate_recording_service = CandidateRecordingService(self)
        self.skill_learning_service = SkillLearningService(self)
        self.action_executor = ActionExecutor(
            self.dict_mgr.noun_dict,
            controller=self.execution_controller,
            app_action_registry=self.app_action_registry,
        )
        self.macro_runner = MacroRunner(self.execution_controller)
        self.dynamic_code_preflight = DynamicCodePreflight()
        self.skill_executor = SkillExecutor(self)
        self.skill_run_policy = SkillRunPolicyService(self)
        
        # Load Builtin Handlers
        self.builtins = BuiltinMacros(self.dict_mgr, self)
        
        # Register command handlers
        self._handlers = {
            "OPEN": self.builtins.handle_open,
            "CLOSE": self.builtins.handle_close,
            "SEARCH": self.builtins.handle_search,
            "PLAYPAUSE": self.builtins.handle_playpause,
            "VOL_UP": self.builtins.handle_vol_up,
            "VOL_DOWN": self.builtins.handle_vol_down,
            "MUTE": self.builtins.handle_mute,
            "SHUTDOWN": self.builtins.handle_shutdown,
            "CANCEL_SHUTDOWN": self.builtins.handle_cancel_shutdown,
            "TIME": self.builtins.handle_time,
            "WEATHER": self.builtins.handle_weather
        }

    @staticmethod
    def _latest_user_text(user_input):
        """Extract the latest user message from a question-mode history payload."""
        if not isinstance(user_input, list):
            return str(user_input or "").strip()
        for item in reversed(user_input):
            if not isinstance(item, dict):
                continue
            if str(item.get("role", "")).casefold() == "user":
                return str(item.get("content", "") or "").strip()
        return ""

    @staticmethod
    def _is_current_date_question(text):
        """Recognize narrow current-date questions that must use the PC clock."""
        compact = re.sub(r"\s+", "", str(text or "").casefold())
        if not compact or any(word in compact for word in ("어제", "내일", "과거", "미래")):
            return False
        has_current_marker = any(word in compact for word in ("오늘", "현재날짜", "지금날짜"))
        has_date_subject = any(
            word in compact
            for word in ("날짜", "며칠", "몇일", "몇월몇일", "무슨요일", "오늘이언제")
        )
        return has_current_marker and has_date_subject and len(compact) <= 40
        
    def normalize_text(self, text):
        return self.local_command_analyzer.normalize_text(text)

    def _make_unique_macro_name(self, app_name, suggested_name):
        return self.skill_learning_service.make_unique_macro_name(
            app_name, suggested_name
        )

    def get_pending_learning_review(self):
        return self.skill_learning_service.get_pending_review()

    @staticmethod
    def _clean_review_macro_name(value):
        return SkillLearningService.clean_review_macro_name(value)

    def _apply_learning_review_edits(self, edits):
        return self.skill_learning_service.apply_review_edits(edits)

    def approve_pending_learning(self, edits=None):
        return self.skill_learning_service.approve(edits)

    def _approve_pending_learning_locked(self, edits=None):
        return self.skill_learning_service.approve_locked(edits)

    def reject_pending_learning(self, reason="discard"):
        return self.skill_learning_service.reject(reason)

    def cancel_current_execution(self):
        return self.execution_controller.cancel()

    def get_execution_diagnostics(self, limit=20):
        return self.execution_controller.diagnostics(limit)

    def get_native_action_candidates(
        self, include_observing=True, include_dismissed=False
    ):
        return self.candidate_recording_service.list_candidates(
            include_observing=include_observing,
            include_dismissed=include_dismissed,
        )

    def set_native_action_candidate_status(self, candidate_id, status, reason=""):
        return self.candidate_recording_service.set_status(
            candidate_id, status, reason=reason
        )

    def get_native_action_candidate_spec(self, candidate_id):
        return self.candidate_recording_service.get_implementation_spec(
            candidate_id
        )

    def _candidate_manager(self):
        return self.candidate_recording_service.manager()

    def _candidate_execution_id(self):
        return self.candidate_recording_service.execution_id()

    def _record_native_candidate_success(
        self, app_name, learned_macro, *, source="learned_dynamic",
        execution_result=None,
    ):
        return self.candidate_recording_service.record_success(
            app_name,
            learned_macro,
            source=source,
            execution_result=execution_result,
        )

    def _record_native_candidate_failure(
        self, app_name, learned_macro, *, source="learned_dynamic"
    ):
        return self.candidate_recording_service.record_failure(
            app_name, learned_macro, source=source
        )

    def get_pending_confirmation(self, session_id=None):
        return self.confirmation_response_handler.get_pending(session_id)

    def _current_execution_id(self):
        current = self.execution_controller.current
        return current.get("execution_id", "") if isinstance(current, dict) else ""

    def _confirmation_result(self, record, message=None):
        return self.confirmation_response_handler.confirmation_result(
            record, message
        )

    def _queue_demo_confirmation(self, original_command, session_id):
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id(),
            original_command=original_command,
            reason="confirmation_demo",
            message="확인 카드 테스트를 계속할까요? 외부 파일이나 프로그램은 변경하지 않습니다.",
            action="confirmation_demo",
            options=[
                {
                    "id": "continue",
                    "label": "계속",
                    "description": "아무 작업도 변경하지 않고 확인 흐름만 완료합니다.",
                    "recommended": True,
                    "aliases": ["예", "네", "응", "ㅇㅇ", "진행", "계속해"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "테스트를 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마"],
                },
            ],
            payload={"kind": "demo"},
        )
        return self._confirmation_result(record)

    def _queue_command_macro_confirmation(
        self, macro_name, macro_data, original_command, session_id
    ):
        command = str(macro_data.get("data", "")).strip()
        try:
            self.builtins.validate_command(command)
        except (TypeError, ValueError) as error:
            return failure_result(
                str(error),
                action="command_line",
                target=command or macro_name,
                error_type="validation_error",
                status="blocked",
            )
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id(),
            original_command=original_command,
            reason="external_program",
            message=(
                "등록한 프로그램 실행 매크로를 실행할까요?\n"
                f"실행 내용: {command}"
            ),
            action="command_line",
            target=command,
            options=[
                {
                    "id": "run",
                    "label": "실행",
                    "description": "표시된 프로그램과 인수를 한 번 실행합니다.",
                    "danger": True,
                    "aliases": ["실행해", "진행", "계속"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "프로그램을 실행하지 않습니다.",
                    "recommended": True,
                    "cancel": True,
                    "aliases": ["취소해", "그만", "하지마"],
                },
            ],
            payload={
                "kind": "command_macro",
                "macro_name": str(macro_name or "")[:200],
                "command": command,
            },
        )
        return self._confirmation_result(record)

    _parse_native_excel_write_command = staticmethod(parse_native_excel_write_command)

    _parse_native_excel_sum_command = staticmethod(parse_native_excel_sum_command)

    _parse_native_excel_format_command = staticmethod(parse_native_excel_format_command)

    _parse_native_excel_range_format_command = staticmethod(parse_native_excel_range_format_command)

    _parse_native_excel_filter_command = staticmethod(parse_native_excel_filter_command)

    _parse_native_excel_find_replace_command = staticmethod(parse_native_excel_find_replace_command)

    _parse_native_excel_sort_command = staticmethod(parse_native_excel_sort_command)

    _parse_native_hwp_find_replace_command = staticmethod(parse_native_hwp_find_replace_command)

    _parse_native_hwp_text_format_command = staticmethod(parse_native_hwp_text_format_command)

    _parse_native_hwp_paragraph_format_command = staticmethod(parse_native_hwp_paragraph_format_command)

    _parse_native_hwp_insert_command = staticmethod(parse_native_hwp_insert_command)

    _parse_native_hwp_save_command = staticmethod(parse_native_hwp_save_command)

    def _queue_app_method_choice(
        self, request, session_id, original_command, log_callback=None
    ):
        prepared_actions = {}
        requests = {}
        preparation_errors = []
        for option_id, operation in EXCEL_FORMAT_METHODS.items():
            try:
                candidate = copy.deepcopy(request)
                candidate["operation"] = operation
                prepared = self.app_command_router.prepare(
                    candidate.get("target"), operation, candidate.get("params", {})
                )
                prepared_actions[option_id] = prepared.to_dict()
                requests[option_id] = candidate
            except AppActionAmbiguousTarget as error:
                return self._queue_app_target_choice(
                    request, error, session_id, original_command
                )
            except AppActionError as error:
                preparation_errors.append(error)

        if not prepared_actions:
            return self._app_action_failure(preparation_errors[0])

        representative = PreparedAction.from_dict(
            next(iter(prepared_actions.values()))
        )
        preference = self.preference_manager.decision(
            EXCEL_FORMAT_PREFERENCE_KEY
        )
        preferred_option = (
            preference.get("preferred_method")
            if preference.get("mode") == "recommend" else None
        )
        if preferred_option not in prepared_actions:
            preferred_option = (
                "conditional_format"
                if "conditional_format" in prepared_actions
                else next(iter(prepared_actions))
            )
        if log_callback:
            log_callback(
                f"[Clarification] Excel {representative.sheet}!{representative.target} 표시 방식 선택 필요"
            )
        option_templates = {
            "conditional_format": {
                "label": "계속 적용",
                "description": "값이 바뀌어도 조건에 따라 자동으로 표시합니다.",
                "aliases": [
                    "계속", "자동", "자동으로", "조건부 서식", "조건부서식",
                    "계속 적용", "값이 바뀌어도", "앞으로는 자동으로",
                ],
            },
            "direct_format": {
                "label": "지금만 표시",
                "description": "현재 조건에 맞는 셀만 한 번 색칠합니다.",
                "aliases": [
                    "지금만", "이번만", "한 번만", "한번만", "현재 값만",
                    "지금만 표시", "앞으로는 지금만",
                ],
            },
        }
        options = []
        for option_id in EXCEL_FORMAT_METHODS:
            if option_id not in prepared_actions:
                continue
            template = option_templates[option_id]
            options.append({
                "id": option_id,
                **template,
                "recommended": option_id == preferred_option,
            })
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "Excel을 변경하지 않습니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        disabled_note = (
            " 이전 자동 적용이 연속으로 실패해 다시 선택을 요청합니다."
            if preference.get("reason") == "auto_apply_disabled_after_failures"
            else ""
        )
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or "",
            original_command=original_command,
            reason="persistent_vs_once",
            message=(
                "값이 바뀔 때 색상도 자동으로 바뀌게 할까요? "
                f"대상: {representative.workbook_name} / {representative.sheet} / "
                f"{representative.target}.{disabled_note}"
            ),
            action="app_command",
            target=(
                f"{representative.workbook_name}/{representative.sheet}/{representative.target}"
            ),
            options=options,
            rememberable=True,
            payload={
                "kind": "app_method_choice",
                "requests": requests,
                "prepared_actions": prepared_actions,
                "preference_key": EXCEL_FORMAT_PREFERENCE_KEY,
            },
        )
        return self._confirmation_result(record)

    def _handle_format_method_request(
        self, request, session_id, original_command, log_callback=None
    ):
        preference = self.preference_manager.decision(
            EXCEL_FORMAT_PREFERENCE_KEY
        )
        preferred_method = preference.get("preferred_method")
        operation = EXCEL_FORMAT_METHODS.get(preferred_method)
        if preference.get("mode") == "auto" and operation:
            candidate = copy.deepcopy(request)
            candidate["operation"] = operation
            candidate["_auto_preference"] = {
                "key": EXCEL_FORMAT_PREFERENCE_KEY,
                "method": preferred_method,
                "base_request": copy.deepcopy(request),
            }
            if log_callback:
                log_callback(
                    f"[Preference] {preferred_method} 선호를 자동 적용합니다."
                )
            return self._execute_native_app_command(
                candidate,
                session_id,
                original_command,
                log_callback=log_callback,
            )
        return self._queue_app_method_choice(
            request,
            session_id,
            original_command,
            log_callback=log_callback,
        )

    def _queue_hwp_scope_choice(
        self, request, session_id, original_command, log_callback=None
    ):
        requests = {}
        prepared_actions = {}
        options = []
        errors = []
        templates = {
            "selection": {
                "label": "선택 영역만",
                "description": "현재 한글에서 선택한 텍스트 안에서만 바꿉니다.",
                "aliases": ["선택", "선택 영역", "선택한 곳", "여기만"],
            },
            "document": {
                "label": "현재 문서 전체",
                "description": "현재 활성 한글 문서 전체에서 일치하는 항목을 바꿉니다.",
                "aliases": ["문서", "문서 전체", "전체", "현재 문서"],
                "danger": True,
            },
        }
        for scope in ("selection", "document"):
            candidate = copy.deepcopy(request)
            candidate["operation"] = "find_replace"
            candidate["params"]["scope"] = scope
            try:
                prepared = self.app_command_router.prepare(
                    "hwp", "find_replace", candidate["params"]
                )
            except AppActionError as error:
                errors.append(error)
                continue
            requests[scope] = candidate
            prepared_actions[scope] = prepared.to_dict()
            template = templates[scope]
            options.append({
                "id": scope,
                **template,
                "description": (
                    f"{template['description']} 예상 변경: "
                    f"{prepared.current_state.get('matching_count', 0)}개"
                ),
                "recommended": scope == "selection",
            })
        if not prepared_actions:
            return self._app_action_failure(errors[0])
        if not any(item.get("recommended") for item in options):
            options[0]["recommended"] = True
        representative = PreparedAction.from_dict(
            next(iter(prepared_actions.values()))
        )
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "한글 문서를 변경하지 않습니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        if log_callback:
            log_callback("[Clarification] 한글 찾기·바꾸기 범위 선택 필요")
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or "",
            original_command=original_command,
            reason="ambiguous_scope",
            message=(
                "어느 범위에서 찾기·바꾸기를 실행할까요? "
                f"대상 문서: {representative.workbook_name}"
            ),
            action="app_command",
            target=representative.workbook_name,
            options=options,
            payload={
                "kind": "hwp_scope_choice",
                "requests": requests,
                "prepared_actions": prepared_actions,
            },
        )
        return self._confirmation_result(record)

    def _queue_app_target_choice(
        self, request, ambiguity, session_id, original_command
    ):
        candidate_requests = {}
        candidate_prepared = {}
        options = []
        representative_operation = request.get("operation")
        if representative_operation == "choose_format_method":
            representative_operation = "apply_conditional_format"
        try:
            for index, column in enumerate(ambiguity.candidates):
                option_id = f"column_{column.lower()}"
                candidate = copy.deepcopy(request)
                candidate["params"].pop("source_range", None)
                candidate["params"]["column_name"] = column
                prepared = self.app_command_router.prepare(
                    candidate.get("target"),
                    representative_operation,
                    candidate.get("params", {}),
                )
                candidate_requests[option_id] = candidate
                candidate_prepared[option_id] = prepared.to_dict()
                options.append({
                    "id": option_id,
                    "label": f"{column}열",
                    "description": f"{column}열의 데이터를 대상으로 사용합니다.",
                    "recommended": index == 0,
                    "aliases": [
                        f"{column}열", column, f"{index + 1}번째", f"{index + 1}번",
                    ],
                })
        except AppActionError as error:
            return self._app_action_failure(error)
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "Excel을 변경하지 않습니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or "",
            original_command=original_command,
            reason="ambiguous_target",
            message=str(ambiguity),
            action="app_command",
            target=ambiguity.target_name or "excel_column",
            options=options,
            payload={
                "kind": "app_target_choice",
                "requests": candidate_requests,
                "prepared_actions": candidate_prepared,
            },
        )
        return self._confirmation_result(record)

    def _prepared_action_success(
        self,
        prepared,
        execution_result,
        confirmation_id=None,
        preference_selection=None,
    ):
        return self.app_command_router.build_success(
            prepared,
            execution_result,
            confirmation_id=confirmation_id,
            preference_selection=preference_selection,
        )

    def _app_action_failure(self, error, target=None):
        return self.app_command_router.failure(error, target)

    def _queue_prepared_action_confirmation(
        self,
        prepared,
        request,
        decision,
        session_id,
        original_command,
        execution_id="",
        preference_selection=None,
    ):
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=(
                self._current_execution_id() or str(execution_id or "")
            ),
            original_command=original_command,
            reason=decision.reason or "destructive_action",
            message=decision.message or "준비된 앱 작업을 실행할까요?",
            action="app_command",
            target=f"{prepared.workbook_name}/{prepared.sheet}/{prepared.target}",
            options=decision.options,
            payload={
                "kind": "prepared_app_action",
                "request": copy.deepcopy(request),
                "prepared_action": prepared.to_dict(),
                "preference_selection": copy.deepcopy(preference_selection),
            },
        )
        return self._confirmation_result(record)

    def _queue_changed_app_context(
        self,
        request,
        previous,
        session_id,
        original_command,
        execution_id="",
        preference_selection=None,
    ):
        return self.app_command_router.queue_changed_context(
            request,
            previous,
            session_id,
            original_command,
            execution_id=execution_id,
            preference_selection=preference_selection,
        )

    def _execute_native_app_command(
        self, request, session_id, original_command, log_callback=None
    ):
        return self.app_command_router.execute(
            request,
            session_id,
            original_command,
            log_callback=log_callback,
        )

    @staticmethod
    def _authorize_plan_overwrite(plan, action, confirmed_target):
        authorized = copy.deepcopy(plan)
        expected = os.path.normcase(os.path.normpath(str(confirmed_target or "")))
        for step in authorized:
            if not isinstance(step, dict) or step.get("action") != action:
                continue
            target = os.path.normpath(os.path.abspath(os.path.expanduser(
                str(step.get("target", "")).strip().strip('"')
            )))
            if action in {"copy_file", "move_file"}:
                destination = os.path.normpath(os.path.abspath(os.path.expanduser(
                    str(step.get("text", "")).strip().strip('"')
                )))
                candidate = (
                    os.path.join(destination, os.path.basename(target))
                    if os.path.isdir(destination) else destination
                )
            else:
                candidate = target
            if os.path.normcase(os.path.normpath(candidate)) == expected:
                step["overwrite"] = True
                return authorized
        raise ActionPlanError("확인한 덮어쓰기 대상을 행동 계획에서 찾지 못했습니다.")

    @staticmethod
    def _dynamic_preflight_context(action, app_name, macro_name, target):
        return {
            "action": str(action or "dynamic_code")[:80],
            "app_name": str(app_name or "")[:100],
            "macro_name": str(macro_name or "")[:100],
            "target": str(target or "")[:500],
        }

    def _analyze_dynamic_code(
        self,
        code,
        argument,
        *,
        action,
        app_name,
        macro_name,
        target,
        log_callback=None,
    ):
        context = self._dynamic_preflight_context(
            action, app_name, macro_name, target
        )
        result = self.dynamic_code_preflight.analyze(
            code, argument=argument, context=context
        )
        fingerprint = result.approval_fingerprint(argument, context)
        details = {
            "status": result.status,
            "action": context["action"],
            "app_name": context["app_name"],
            "macro_name": context["macro_name"],
            "code_sha256": result.code_sha256,
            "finding_codes": [item.code for item in result.findings],
        }
        self.execution_controller.event(
            "dynamic_code_preflight", result.status, details
        )
        if log_callback:
            log_callback(
                "[Security] 동적 코드 preflight: "
                f"{result.status} ({context['app_name']}/{context['macro_name']})"
            )
        return result, fingerprint

    @staticmethod
    def _dynamic_preflight_failure(result, action="dynamic_code", target=None):
        reasons = []
        for finding in result.findings:
            if finding.message not in reasons:
                reasons.append(finding.message)
        message = "동적 Python 코드를 안전 정책에서 차단했습니다."
        if reasons:
            message += "\n- " + "\n- ".join(reasons[:5])
        return failure_result(
            message,
            action=action,
            target=target,
            error_type="validation_error",
            status="blocked",
            data={"risk_analysis": result.to_dict()},
        )

    def _queue_dynamic_code_confirmation(
        self,
        items,
        resume_payload,
        session_id,
        original_command,
        prior_approvals=None,
    ):
        reasons = []
        labels = []
        approvals = set(prior_approvals or [])
        analyses = []
        for item in items:
            label = str(item.get("label") or "동적 Python 작업")[:120]
            if label not in labels:
                labels.append(label)
            approvals.add(item["fingerprint"])
            analysis = item["result"].to_dict()
            analysis["label"] = label
            analyses.append(analysis)
            for finding in item["result"].findings:
                if finding.message not in reasons:
                    reasons.append(finding.message)
        message = "동적 Python 코드가 외부 상태를 변경할 수 있습니다. 이번에만 실행할까요?"
        if labels:
            message += "\n대상: " + ", ".join(labels[:4])
        if reasons:
            message += "\n- " + "\n- ".join(reasons[:5])
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id(),
            original_command=original_command,
            reason="dynamic_code_risk",
            message=message,
            action="dynamic_code",
            target=", ".join(labels[:4]) or "동적 Python 작업",
            options=[
                {
                    "id": "run_once",
                    "label": "이번만 실행",
                    "description": "표시된 위험을 승인하고 현재 코드와 인자로 한 번만 실행합니다.",
                    "danger": True,
                    "aliases": ["예", "네", "응", "ㅇㅇ", "실행", "계속", "이번만"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "코드를 실행하지 않고 외부 상태를 그대로 유지합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마", "취소"],
                },
            ],
            payload={
                **copy.deepcopy(resume_payload),
                "kind": "dynamic_code_preflight",
                "approval_fingerprints": sorted(approvals),
                "risk_analyses": analyses,
            },
        )
        return self._confirmation_result(record)

    def _queue_local_learned_dynamic_confirmation(
        self,
        app_name,
        macro_name,
        argument,
        result,
        fingerprint,
        session_id,
        original_command,
    ):
        return self._queue_dynamic_code_confirmation(
            [{
                "label": f"저장된 매크로 {app_name}/{macro_name}",
                "fingerprint": fingerprint,
                "result": result,
            }],
            {
                "mode": "local_learned",
                "app_name": app_name,
                "macro_name": macro_name,
                "argument": argument,
                "expected_code_sha256": result.code_sha256,
            },
            session_id,
            original_command,
        )

    def _resume_local_learned_dynamic(
        self, payload, confirmation_id, log_callback=None
    ):
        app_name = payload.get("app_name")
        macro_name = payload.get("macro_name")
        learned = self._get_learned_macro(app_name, macro_name)
        if not learned or not learned.get("code"):
            return failure_result(
                "확인 후 저장된 매크로를 다시 찾지 못했습니다. 실행하지 않았습니다.",
                action="learned_macro",
                target=macro_name,
                error_type="target_not_found",
            )
        argument = payload.get("argument", "")
        try:
            execution_result = self.skill_executor.execute(
                app_name,
                macro_name,
                skill=learned,
                argument=argument,
                source="confirmation_resume",
                action="use_learned_macro",
                target=learned.get("default_target", ""),
                approved_fingerprints=payload.get("approval_fingerprints", []),
                expected_code_sha256=payload.get("expected_code_sha256", ""),
                log_callback=log_callback,
            )
            verified = bool(execution_result.get("verified", False))
            return success_result(
                f"확인한 저장 매크로 [{macro_name}]를 이번에만 실행했습니다.",
                action="learned_macro",
                target=macro_name,
                verified=verified,
                verification_status=execution_result.get(
                    "verification_status",
                    "verified" if verified else "confirmation_required",
                ),
                data={
                    "app": app_name,
                    "confirmation_id": confirmation_id,
                    "execution_result": execution_result,
                },
            )
        except SkillPreflightBlocked as error:
            return self._dynamic_preflight_failure(
                error.preflight, action="learned_macro", target=macro_name
            )
        except (SkillConfirmationRequired, SkillContextChanged):
            return failure_result(
                "확인한 뒤 매크로 코드나 실행 인자가 달라져 실행하지 않았습니다. 명령을 다시 요청해주세요.",
                action="learned_macro",
                target=macro_name,
                error_type="validation_error",
                status="context_changed",
            )
        except ExecutionCancelled:
            raise
        except Exception as error:
            failure_type = self._failure_type_for_error(error)
            return failure_result(
                f"확인한 저장 매크로를 실행하지 못했습니다: {error}",
                action="learned_macro",
                target=macro_name,
                error_type=failure_type,
                failed_step=getattr(error, "failed_step", None),
                retryable=getattr(error, "retryable", False),
                status=getattr(error, "status", "failed"),
            )

    def _skill_policy_preview_result(
        self, app_name, macro_name, skill, assessment
    ):
        plan = skill.get({
            "native": "native_plan",
            "action_plan": "plan",
            "uia": "uia_plan",
        }.get(assessment.route, ""), [])
        return success_result(
            f"[{macro_name}] 작업은 실행하지 않았습니다. 실행 경로와 정책만 미리 보여드립니다.",
            action="learned_macro_preview",
            target=macro_name,
            verified=True,
            data={
                "app": app_name,
                "macro_name": macro_name,
                "route": assessment.route,
                "run_policy": assessment.run_policy,
                "step_count": len(plan) if isinstance(plan, list) else 0,
                "forced_confirmation_reasons": list(
                    assessment.forced_reasons
                ),
                "executed": False,
            },
        )

    def _queue_skill_run_policy_confirmation(
        self,
        *,
        app_name,
        macro_name,
        skill,
        assessment,
        session_id,
        original_command,
        resume_payload,
    ):
        options = [{
            "id": "run_once",
            "label": "이번만 실행",
            "description": "저장된 실행 정책은 바꾸지 않고 이번 요청만 실행합니다.",
            "recommended": True,
            "aliases": ["이번만", "한 번만", "실행", "진행", "예", "네", "응"],
        }]
        if assessment.suggest_auto and not assessment.forced_reasons:
            options.append({
                "id": "enable_auto",
                "label": "앞으로 자동 실행",
                "description": (
                    "사용자가 승인한 뒤에만 이 스킬의 run_policy를 auto로 변경합니다."
                ),
                "aliases": [
                    "앞으로 자동", "항상 자동", "자동 실행", "묻지 마",
                ],
            })
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "스킬을 실행하지 않고 저장된 정책도 유지합니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        if assessment.suggest_auto:
            message = (
                f"[{macro_name}] 작업은 최근 "
                f"{assessment.consecutive_verified_success}회 연속 자동 검증에 "
                "성공했습니다. 이번만 실행하거나, 앞으로 확인 없이 자동 실행하도록 "
                "직접 승인할 수 있습니다."
            )
        elif assessment.forced_reasons:
            message = (
                f"[{macro_name}] 작업은 안전상 항상 실행 확인이 필요합니다. "
                f"사유: {', '.join(assessment.forced_reasons)}"
            )
        else:
            message = f"저장된 학습 행동 [{macro_name}]을 이번에 실행할까요?"
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or "",
            original_command=original_command,
            reason=assessment.reason,
            message=message,
            action="learned_macro_run_policy",
            target=macro_name,
            options=options,
            payload={
                "kind": "skill_run_policy",
                "app_name": app_name,
                "macro_name": macro_name,
                "skill_fingerprint": skill_policy_fingerprint(skill),
                "assessment": assessment.to_dict(),
                **copy.deepcopy(resume_payload),
            },
        )
        return self._confirmation_result(record)

    def _local_skill_policy_gate(
        self,
        *,
        app_name,
        macro_name,
        skill,
        slots,
        argument,
        session_id,
        original_command,
    ):
        assessment = self.skill_run_policy.assess(skill, original_command)
        if assessment.directive == DIRECTIVE_PREVIEW:
            return self._skill_policy_preview_result(
                app_name, macro_name, skill, assessment
            )
        if assessment.route == "python":
            decision = self.skill_executor.preflight(
                app_name,
                macro_name,
                skill=skill,
                argument=argument,
                action="use_learned_macro",
                target=skill.get("default_target", ""),
            )
            if decision.status == BLOCKED:
                return self._dynamic_preflight_failure(
                    decision.result,
                    action="learned_macro",
                    target=macro_name,
                )
            if decision.status == CONFIRMATION_REQUIRED:
                return self._queue_local_learned_dynamic_confirmation(
                    app_name,
                    macro_name,
                    argument,
                    decision.result,
                    decision.fingerprint,
                    session_id,
                    original_command,
                )
        if not assessment.requires_confirmation:
            return None
        return self._queue_skill_run_policy_confirmation(
            app_name=app_name,
            macro_name=macro_name,
            skill=skill,
            assessment=assessment,
            session_id=session_id,
            original_command=original_command,
            resume_payload={
                "resume_mode": "local",
                "slots": dict(slots or {}),
                "argument": argument,
                "source": "local_match_policy_resume",
                "target": skill.get("default_target", ""),
            },
        )

    def _queue_action_plan_confirmation(
        self, error, payload, session_id, original_command
    ):
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or payload.get("execution_id", ""),
            original_command=original_command,
            reason="destructive_action",
            message=f"대상 파일이 이미 있습니다. 기존 파일을 덮어쓸까요?\n{error.target}",
            action=error.action,
            target=error.target,
            options=[
                {
                    "id": "overwrite",
                    "label": "덮어쓰기",
                    "description": "기존 파일을 새 내용으로 교체합니다.",
                    "danger": True,
                    "aliases": ["예", "네", "응", "덮어써", "교체", "진행"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "원본 파일을 유지하고 작업을 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마"],
                },
            ],
            payload={
                **copy.deepcopy(payload),
                "kind": "action_plan_overwrite",
                "confirmation_action": error.action,
                "confirmation_target": error.target,
            },
        )
        return self._confirmation_result(record)

    def _queue_uia_target_choice(
        self, ambiguity, payload, session_id, original_command
    ):
        selectors = {}
        options = []
        for index, candidate in enumerate(ambiguity.candidates, start=1):
            option_id = f"uia_target_{index}"
            selector = candidate.get("selector")
            if not isinstance(selector, dict):
                continue
            selectors[option_id] = copy.deepcopy(selector)
            name = str(candidate.get("name") or "(이름 없음)")
            control_type = str(candidate.get("control_type") or "Unknown")
            context = str(
                candidate.get("parent_name")
                or candidate.get("ancestor_name")
                or "상위 정보 없음"
            )
            automation_id = str(candidate.get("automation_id") or "")
            detail = f"{control_type} · {context}"
            if automation_id:
                detail += f" · ID {automation_id}"
            options.append({
                "id": option_id,
                "label": f"{index}. {name}"[:80],
                "description": detail,
                "aliases": [
                    str(index), f"{index}번", f"{index}번째", name,
                ],
            })
        if not selectors:
            return failure_result(
                "UI 요소 후보의 식별 정보를 만들 수 없어 실행하지 않았습니다.",
                action="action_plan",
                error_type="validation_error",
                status="ambiguous_target",
            )
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "어떤 UI 요소도 선택하거나 클릭하지 않습니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        record = self.pending_confirmation_manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._current_execution_id() or payload.get(
                "execution_id", ""
            ),
            original_command=original_command,
            reason="ambiguous_target",
            message=str(ambiguity),
            action="uia_target_choice",
            target=(
                ambiguity.diagnostic.get("matched_name")
                if isinstance(ambiguity.diagnostic, dict) else None
            ),
            options=options,
            payload={
                **copy.deepcopy(payload),
                "kind": "uia_target_choice",
                "candidate_selectors": selectors,
                "diagnostic": copy.deepcopy(
                    getattr(ambiguity, "diagnostic", {})
                ),
            },
        )
        return self._confirmation_result(record)

    def _queue_learned_uia_target_choice(
        self,
        ambiguity,
        *,
        app_name,
        macro_name,
        skill,
        slots,
        session_id,
        original_command,
        argument="",
        source="confirmation_resume",
        target="",
    ):
        diagnostic = getattr(ambiguity, "skill_execution", {})
        route = (
            diagnostic.get("selected_route")
            if isinstance(diagnostic, dict) else ""
        )
        plan_key = {
            "native": "native_plan",
            "action_plan": "plan",
            "uia": "uia_plan",
        }.get(route)
        plan = skill.get(plan_key) if plan_key and isinstance(skill, dict) else None
        if not isinstance(plan, list) or not plan:
            return failure_result(
                "모호한 UI 요소가 발생한 학습 경로를 복원할 수 없습니다.",
                action="learned_macro",
                target=macro_name,
                error_type="validation_error",
                status="context_changed",
            )
        return self._queue_uia_target_choice(
            ambiguity,
            {
                "resume_mode": "learned_skill",
                "plan": plan,
                "plan_key": plan_key,
                "failed_step": getattr(ambiguity, "failed_step", 1),
                "slots": dict(slots or {}),
                "learned_skill": copy.deepcopy(skill),
                "app_name": app_name,
                "macro_name": macro_name,
                "argument": argument,
                "source": source,
                "target": target,
            },
            session_id,
            original_command,
        )

    def resolve_pending_confirmation(
        self,
        session_id,
        confirmation_id=None,
        option_id=None,
        user_text=None,
        log_callback=None,
        remember_preference=False,
    ):
        return self.confirmation_dispatcher.resolve(
            session_id,
            confirmation_id=confirmation_id,
            option_id=option_id,
            user_text=user_text,
            log_callback=log_callback,
            remember_preference=remember_preference,
        )

    @staticmethod
    def _failure_type_for_error(error):
        explicit = getattr(error, "error_type", None)
        if explicit:
            return normalize_error_type(explicit)
        if isinstance(error, ExecutionCancelled):
            return "user_cancelled"
        if isinstance(error, MacroTimeoutError):
            return "timeout"
        if isinstance(error, ActionPlanVerificationError):
            return "verification_error"
        if isinstance(error, (FileNotFoundError, KeyError)):
            return "target_not_found"
        if isinstance(error, (ActionPlanError, ValueError, TypeError)):
            return "validation_error"
        return "execution_error"

    def retry_learned_macro_step(self, app_name, macro_name, step_number):
        app_macros = getattr(self.dict_mgr, "learned_macros", {}).get(app_name, {})
        learned = app_macros.get(macro_name) if isinstance(app_macros, dict) else None
        if not isinstance(learned, dict) or not learned.get("plan"):
            raise ValueError("단계 재시도가 가능한 학습 행동을 찾지 못했습니다.")
        try:
            step_number = int(step_number)
        except (TypeError, ValueError):
            raise ValueError("재시도 단계 번호가 올바르지 않습니다.")
        self.execution_controller.begin(
            f"retry:{app_name}/{macro_name}", {"start_step": step_number}
        )
        try:
            slots = self._build_slot_values(learned.get("learning", {}))
            result = self.skill_executor.execute(
                app_name,
                macro_name,
                skill=learned,
                slots=slots,
                source="retry",
                start_step=step_number,
                retry_attempts=1,
                record_candidate=False,
            )
            self.execution_controller.finish(True, extra={"result": result})
            return result
        except ExecutionCancelled as error:
            self.execution_controller.finish(False, "cancelled", error=str(error))
            raise
        except Exception as error:
            failure_type = self._failure_type_for_error(error)
            self.execution_controller.finish(
                False, "failed", error=str(error),
                extra={
                    "failed_step": getattr(error, "failed_step", step_number),
                    "retryable": getattr(error, "retryable", False),
                },
            )
            raise

    def parse_and_execute(self, user_input, log_callback=None, image_data=None, mode="command", use_api=False, summary="", stream_callback=None, conversation_state=None, session_id=None):
        """Compatibility API returning only the user-facing message."""
        return self.execute_command_result(
            user_input, log_callback, image_data, mode, use_api, summary,
            stream_callback, conversation_state, session_id,
        )["message"]

    def execute_command_result(self, user_input, log_callback=None, image_data=None, mode="command", use_api=False, summary="", stream_callback=None, conversation_state=None, session_id=None):
        """Execute one command and always return the canonical result object."""
        try:
            raw = self._parse_and_execute_core(
                user_input, log_callback, image_data, mode, use_api, summary,
                stream_callback, conversation_state, session_id,
            )
            return normalize_execution_result(raw)
        except ExecutionCancelled as error:
            return failure_result(
                str(error), action="command", error_type="user_cancelled",
            )
        except Exception as error:
            return failure_result(
                str(error), action="command",
                error_type=self._failure_type_for_error(error),
                failed_step=getattr(error, "failed_step", None),
                retryable=getattr(error, "retryable", False),
            )

    def _parse_and_execute_core(
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
    ):
        return self.command_pipeline.execute(
            user_input,
            log_callback=log_callback,
            image_data=image_data,
            mode=mode,
            use_api=use_api,
            summary=summary,
            stream_callback=stream_callback,
            conversation_state=conversation_state,
            session_id=session_id,
        )

    def _execute_ai_action_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input_str,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        return self.ai_action_handler.execute_batch(
            response,
            actions,
            validation_issues,
            user_input_str,
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            confirmation_id=confirmation_id,
            image_data=image_data,
            use_api=use_api,
        )

    # ================= Helper Methods =================
    def analyze_command(self, user_input):
        return self.local_command_analyzer.analyze_command(user_input)

    def _analyze_single_command(self, text, template_match=None):
        return self.local_command_analyzer.analyze_single_command(
            text, template_match=template_match
        )

    def _build_dynamic_argument(self, learning, target, app_name):
        if learning.get("argument_mode") != "json":
            return str(target or "")
        values = {
            item["name"]: item.get("value", "")
            for item in learning.get("slots", [])
            if isinstance(item, dict) and item.get("name")
        }
        values.setdefault("_target", str(target or ""))
        values.setdefault("_app", str(app_name or ""))
        return json.dumps(values, ensure_ascii=False)

    def _build_slot_values(
        self, learning, template_match=None, matched_app_name=None, matched_app_path=None
    ):
        values = {
            item["name"]: item.get("value", "")
            for item in learning.get("slots", [])
            if isinstance(item, dict) and item.get("name")
        }
        if template_match:
            values.update(template_match.get("slots", {}))
        if matched_app_name:
            for app_key in ("app", "app_name"):
                if app_key in values:
                    values[app_key] = matched_app_name
            values.setdefault("app", matched_app_name)
        if matched_app_path:
            values["_app_path"] = matched_app_path
        return values

    def _build_learned_argument(
        self, learned_macro, template_match, matched_app_name, matched_app_path
    ):
        learning = learned_macro.get("learning", {})
        if not isinstance(learning, dict) or learning.get("argument_mode") != "json":
            return str(learned_macro.get("default_target") or (
                matched_app_path if matched_app_path else (matched_app_name or "")
            ))

        values = self._build_slot_values(
            learning, template_match, matched_app_name, matched_app_path
        )
        values["_target"] = str(learned_macro.get("default_target", ""))
        values["_app"] = str(matched_app_name or learned_macro.get("app", ""))
        values["_app_path"] = str(matched_app_path or "")
        return json.dumps(values, ensure_ascii=False)

    def _validate_llm_result(self, result):
        return self.ai_action_handler.validate_result(result)

    def _resolve_registered_app(self, target):
        return self.local_command_analyzer.resolve_registered_app(target)

    def _get_learned_macro(self, app_name, macro_name):
        if not isinstance(app_name, str) or not isinstance(macro_name, str):
            return None
        learned_macros = getattr(self.dict_mgr, "learned_macros", {})
        app_macros = learned_macros.get(app_name)
        if not isinstance(app_macros, dict):
            return None
        macro = app_macros.get(macro_name)
        if not isinstance(macro, dict) or macro.get("state", "active") != "active":
            return None
        return macro

    def _validate_generated_code(self, act, require_external_target=False):
        return self.ai_action_handler._validate_generated_code(
            act, require_external_target=require_external_target
        )

    def _identify_macro(self, text):
        return self.local_command_analyzer.identify_macro(text)

    def _identify_app(self, text, normalized_tokens, log_callback, matched_macro=None):
        return self.local_command_analyzer.identify_app(
            text, normalized_tokens, log_callback, matched_macro
        )

    def _ensure_app_index(self):
        return self.local_command_analyzer.ensure_app_index()

    def _build_app_candidates(self, normalized_tokens):
        return self.local_command_analyzer.build_app_candidates(normalized_tokens)

    def _looks_like_implicit_search(self, text):
        return self.local_command_analyzer.looks_like_implicit_search(text)

    def _split_compound_command(self, text):
        return self.local_command_analyzer.split_compound_command(text)

    def _can_handle_locally(self, text):
        return self.local_command_analyzer.can_handle_locally(text)

    def _execute_local_compound(self, parts, log_callback, image_data, use_api):
        results = []
        step_results = []

        for index, part in enumerate(parts, start=1):
            if log_callback:
                log_callback(f"[Compound] {index}/{len(parts)} 단계 실행: {part}")
            step_result = self.execute_command_result(
                part,
                log_callback=log_callback,
                image_data=image_data,
                mode="command",
                use_api=use_api,
            )
            step_results.append(step_result)
            status = "✅" if step_result["success"] else "❌"
            results.append(f"{status} {index}. {part}: {step_result['message']}")
            if not step_result["success"]:
                return failure_result(
                    "연속 명령 처리 결과\n" + "\n".join(results),
                    action="compound",
                    error_type=step_result.get("error_type", "execution_error"),
                    failed_step=index,
                    retryable=step_result.get("retryable", False),
                    data={"steps": results, "step_results": step_results},
                )

        return success_result(
            "연속 명령 처리 결과\n" + "\n".join(results),
            action="compound",
            verified=all(item.get("verified", False) for item in step_results),
            data={"steps": results, "step_results": step_results},
        )
