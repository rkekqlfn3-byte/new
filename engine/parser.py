import copy
import json
import os
import re
import subprocess  # Public compatibility patch point for older integrations.

from engine.action_executor import (
    ActionExecutor,
    ActionPlanError,
    ActionPlanVerificationError,
)
from engine.ai_actions import AIActionHandler
from engine.app_actions import (
    AppActionRegistry,
    AppCommandRouter,
)
from engine.builtins import BuiltinMacros
from engine.confirmation import ConfirmationRegistry
from engine.decision import DecisionEngine, PreferenceManager
from engine.edit_mode.controller import EditModeController
from engine.execution_result import (
    failure_result,
    normalize_error_type,
    normalize_execution_result,
    success_result,
)
from engine.execution_runtime import ExecutionCancelled, ExecutionController
from engine.llm_engine import LLMEngine
from engine.local_commands import LocalCommandAnalyzer
from engine.macro_runner import MacroRunner, MacroTimeoutError
from engine.managers.dict_manager import DictionaryManager
from engine.managers.native_action_candidate_manager import (
    NativeActionCandidateManager,
)
from engine.parsing.office_command_parser import EXCEL_FORMAT_PREFERENCE_KEY
from engine.pdf.intake import PdfIntakeManager
from engine.pdf.task_service import PdfTaskService
from engine.pipeline import CommandPipeline
from engine.runtime_services import ParserRuntimeServices
from engine.security import DynamicCodePreflight
from engine.skills import (
    CandidateRecordingService,
    LearnedReplayService,
    SkillExecutionServices,
    SkillExecutor,
    SkillLearningService,
    SkillRunPolicyService,
)
from engine.template_matcher import LearnedTemplateMatcher

__all__ = ["CommandParser", "EXCEL_FORMAT_PREFERENCE_KEY", "subprocess"]


class CommandParser:
    @property
    def _native_candidate_manager_injected(self):
        service = getattr(self, "candidate_recording_service", None)
        if service is not None:
            return service.injected
        return getattr(self, "_native_candidate_injected_value", False)

    @_native_candidate_manager_injected.setter
    def _native_candidate_manager_injected(self, value):
        self._native_candidate_injected_value = bool(value)
        service = getattr(self, "candidate_recording_service", None)
        if service is not None:
            service.injected = bool(value)

    @property
    def pending_macros(self):
        service = getattr(self, "skill_learning_service", None)
        if service is not None:
            return service.pending_macros
        return getattr(self, "_pending_macros", [])

    @pending_macros.setter
    def pending_macros(self, value):
        self._pending_macros = value
        service = getattr(self, "skill_learning_service", None)
        if service is not None:
            service.pending_macros = value

    _BUILTIN_HANDLER_NAMES = {
        "OPEN": "handle_open",
        "CLOSE": "handle_close",
        "SEARCH": "handle_search",
        "PLAYPAUSE": "handle_playpause",
        "VOL_UP": "handle_vol_up",
        "VOL_DOWN": "handle_vol_down",
        "VOL_SET": "handle_vol_set",
        "MUTE": "handle_mute",
        "SHUTDOWN": "handle_shutdown",
        "CANCEL_SHUTDOWN": "handle_cancel_shutdown",
        "TIME": "handle_time",
        "DATE": "handle_date",
        "WEATHER": "handle_weather",
    }

    @property
    def builtins(self):
        return self._builtins

    @builtins.setter
    def builtins(self, value):
        self._builtins = value
        handlers = getattr(self, "_handlers", None)
        if handlers is not None:
            for name, method_name in self._BUILTIN_HANDLER_NAMES.items():
                handlers[name] = getattr(value, method_name)
        analyzer = getattr(self, "local_command_analyzer", None)
        if analyzer is not None:
            analyzer.builtins = value

    @property
    def dict_mgr(self):
        return self._dict_mgr

    @dict_mgr.setter
    def dict_mgr(self, value):
        self._dict_mgr = value
        analyzer = getattr(self, "local_command_analyzer", None)
        if analyzer is not None:
            analyzer.dict_mgr = value
        builtins = getattr(self, "builtins", None)
        if builtins is not None:
            builtins.dict_mgr = value
        candidate_service = getattr(self, "candidate_recording_service", None)
        if candidate_service is not None:
            candidate_service.dict_mgr = value
        learning_service = getattr(self, "skill_learning_service", None)
        if learning_service is not None:
            learning_service.dict_mgr = value
        skill_executor = getattr(self, "skill_executor", None)
        if skill_executor is not None:
            skill_executor.services.dict_mgr = value

    @property
    def native_action_candidate_manager(self):
        service = getattr(self, "candidate_recording_service", None)
        if service is not None:
            return service._manager
        return self._native_action_candidate_manager

    @native_action_candidate_manager.setter
    def native_action_candidate_manager(self, value):
        self._native_action_candidate_manager = value
        service = getattr(self, "candidate_recording_service", None)
        if service is not None:
            service._manager = value

    @property
    def execution_controller(self):
        return self._execution_controller

    @execution_controller.setter
    def execution_controller(self, value):
        self._execution_controller = value
        confirmations = getattr(self, "confirmations", None)
        if confirmations is not None:
            confirmations.responses.execution_controller = value
        action_executor = getattr(self, "action_executor", None)
        if action_executor is not None:
            action_executor.controller = value
        macro_runner = getattr(self, "macro_runner", None)
        if macro_runner is not None:
            macro_runner.controller = value
        candidate_service = getattr(self, "candidate_recording_service", None)
        if candidate_service is not None:
            candidate_service.execution_controller = value
        skill_executor = getattr(self, "skill_executor", None)
        if skill_executor is not None:
            skill_executor.services.execution_controller = value
        edit_controller = getattr(self, "edit_mode_controller", None)
        if edit_controller is not None:
            edit_controller._execution_controller = value

    @property
    def app_action_registry(self):
        return self._app_action_registry

    @app_action_registry.setter
    def app_action_registry(self, value):
        self._app_action_registry = value
        router = getattr(self, "app_command_router", None)
        if router is not None:
            router.registry = value
        edit_controller = getattr(self, "edit_mode_controller", None)
        if edit_controller is not None:
            edit_controller._app_action_registry = value

    @property
    def decision_engine(self):
        return self._decision_engine

    @decision_engine.setter
    def decision_engine(self, value):
        self._decision_engine = value
        router = getattr(self, "app_command_router", None)
        if router is not None:
            router.decision_engine = value

    @property
    def preference_manager(self):
        return self._preference_manager

    @preference_manager.setter
    def preference_manager(self, value):
        self._preference_manager = value
        router = getattr(self, "app_command_router", None)
        if router is not None:
            router.preference_manager = value
        skill_executor = getattr(self, "skill_executor", None)
        if skill_executor is not None:
            skill_executor.services.preference_manager = value

    def __init__(
        self,
        native_action_candidate_manager=None,
        edit_mode_controller=None,
        execution_controller=None,
        pdf_intake_manager=None,
    ):
        self.dict_mgr = DictionaryManager()
        self.llm_engine = LLMEngine(self.dict_mgr)
        
        self.template_matcher = LearnedTemplateMatcher()
        self.command_pipeline = CommandPipeline()
        self.execution_controller = execution_controller or ExecutionController()
        self.confirmations = ConfirmationRegistry(self.execution_controller)
        self.ai_action_handler = AIActionHandler()
        self.app_action_registry = AppActionRegistry()
        self.edit_mode_controller = edit_mode_controller or EditModeController()
        self.pdf_intake_manager = pdf_intake_manager or PdfIntakeManager()
        self.pdf_task_service = PdfTaskService(
            self.pdf_intake_manager,
            self.llm_engine,
            self.dict_mgr,
        )
        bind_services = getattr(self.edit_mode_controller, "bind_services", None)
        if callable(bind_services):
            bind_services(
                self.execution_controller,
                self.app_action_registry,
                self.confirmations,
            )
        self.edit_mode_handler = self.edit_mode_controller
        self.decision_engine = DecisionEngine()
        self.preference_manager = PreferenceManager()
        self.app_command_router = AppCommandRouter(
            self.app_action_registry,
            self.decision_engine,
            self.preference_manager,
            self.confirmations,
        )
        self._native_candidate_manager_injected = (
            native_action_candidate_manager is not None
        )
        self.native_action_candidate_manager = (
            native_action_candidate_manager or NativeActionCandidateManager()
        )
        self.candidate_recording_service = CandidateRecordingService(
            self.native_action_candidate_manager,
            self.dict_mgr,
            self.execution_controller,
            injected=self._native_candidate_manager_injected,
        )
        self.skill_learning_service = SkillLearningService(
            self.dict_mgr,
            self.template_matcher,
            self.candidate_recording_service,
        )
        self.learned_replay_service = LearnedReplayService()
        self.action_executor = ActionExecutor(
            self.dict_mgr.noun_dict,
            controller=self.execution_controller,
            app_action_registry=self.app_action_registry,
        )
        self.macro_runner = MacroRunner(self.execution_controller)
        self.dynamic_code_preflight = DynamicCodePreflight()
        self.builtins = BuiltinMacros(self.dict_mgr, self.action_executor)
        self.skill_executor = SkillExecutor(SkillExecutionServices(
            dict_mgr=self.dict_mgr,
            dynamic_code_preflight=self.dynamic_code_preflight,
            execution_controller=self.execution_controller,
            action_executor=self.action_executor,
            app_command_router=self.app_command_router,
            candidate_recording_service=self.candidate_recording_service,
            macro_runner=self.macro_runner,
            confirmations=self.confirmations,
            preference_manager=self.preference_manager,
        ))
        self.skill_run_policy = SkillRunPolicyService()
        
        # Register command handlers
        self._handlers = {
            "OPEN": self.builtins.handle_open,
            "CLOSE": self.builtins.handle_close,
            "SEARCH": self.builtins.handle_search,
            "PLAYPAUSE": self.builtins.handle_playpause,
            "VOL_UP": self.builtins.handle_vol_up,
            "VOL_DOWN": self.builtins.handle_vol_down,
            "VOL_SET": self.builtins.handle_vol_set,
            "MUTE": self.builtins.handle_mute,
            "SHUTDOWN": self.builtins.handle_shutdown,
            "CANCEL_SHUTDOWN": self.builtins.handle_cancel_shutdown,
            "TIME": self.builtins.handle_time,
            "DATE": self.builtins.handle_date,
            "WEATHER": self.builtins.handle_weather
        }
        self.local_command_analyzer = LocalCommandAnalyzer(
            self.dict_mgr,
            self.template_matcher,
            self.builtins,
            self._handlers,
        )

    def _runtime_services(self):
        """Build the explicit port passed below the public parser facade."""
        return ParserRuntimeServices.from_facade(self)

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
        has_current_marker = any(
            word in compact for word in ("오늘", "현재날짜", "지금날짜", "지금")
        )
        has_date_subject = any(
            word in compact
            for word in ("날짜", "며칠", "몇일", "몇월몇일", "무슨요일", "오늘이언제")
        )
        return has_current_marker and has_date_subject and len(compact) <= 40

    @staticmethod
    def _is_current_time_question(text):
        """Recognize requests for the PC's current time, not elapsed duration."""
        compact = re.sub(r"\s+", "", str(text or "").casefold())
        if not compact or any(
            marker in compact
            for marker in ("내일몇시", "어제몇시", "몇시간", "시간걸", "소요시간")
        ):
            return False
        return any(
            marker in compact
            for marker in (
                "지금몇시", "현재몇시", "몇시야", "몇시예요", "몇시인가",
                "지금시간", "현재시간", "시간알려줘", "시간을알려줘",
            )
        ) and len(compact) <= 40

    @staticmethod
    def _is_current_weather_question(text):
        """Recognize narrow current-weather requests that use the local route."""
        compact = re.sub(r"\s+", "", str(text or "").casefold())
        if "날씨" not in compact or len(compact) > 60:
            return False
        if any(
            marker in compact
            for marker in ("날씨란", "날씨의뜻", "과거날씨", "검색", "찾아")
        ):
            return False
        return any(
            marker in compact
            for marker in (
                "오늘날씨", "지금날씨", "현재날씨", "날씨알려", "날씨어때",
                "날씨보여", "날씨확인",
            )
        )
        
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

    def _diagnostic_manager(self):
        manager = getattr(self.execution_controller, "incident_manager", None)
        if manager is None:
            raise RuntimeError("자가 진단 저장소가 비활성화되어 있습니다.")
        return manager

    def get_diagnostic_incidents(self, limit=20, status=None):
        return self._diagnostic_manager().list_incidents(limit, status=status)

    def get_self_diagnostic_report(self, incident_id=None):
        manager = self._diagnostic_manager()
        incident = manager.get(incident_id) if incident_id else manager.latest()
        if incident is None:
            raise ValueError("진단할 실패 기록이 없습니다.")
        return manager.report(incident["incident_id"])

    def get_diagnostic_health_summary(self):
        return self._diagnostic_manager().health_summary()

    def get_developer_issues(self, limit=50, status=None):
        return self._diagnostic_manager().list_developer_issues(
            limit, status=status
        )

    def set_diagnostic_incident_status(self, incident_id, status):
        return self._diagnostic_manager().set_status(incident_id, status)

    def get_remediation_proposal(self, incident_id):
        return self._diagnostic_manager().remediation_spec(incident_id)

    def diagnose_latest_failure(self):
        try:
            report = self.get_self_diagnostic_report()
        except ValueError as error:
            return failure_result(
                str(error), action="self_diagnosis",
                error_type="target_not_found", status="not_found",
            )
        where = report["where"]
        failed_step = where.get("failed_step") or where.get("operation") or "미확인"
        analysis = report["why"]
        triage = report.get("triage", {})
        tests = ", ".join(report["remediation"]["required_regression_tests"])
        return success_result(
            "최근 실패 진단\n"
            f"- 실패 지점: {failed_step}\n"
            f"- 원인 분류: {analysis['headline']}\n"
            f"- 책임 분류: {triage.get('category', 'unknown')} "
            f"(담당: {triage.get('owner', 'unknown')})\n"
            f"- 다음 조치: {triage.get('next_action', 'collect_more_evidence')}\n"
            f"- 수정 제안: {analysis['recommended_fix']}\n"
            f"- 필요한 검증: {tests}\n"
            "코드 수정과 EXE 빌드는 자동 실행하지 않으며 별도 승인이 필요합니다.",
            action="self_diagnosis",
            verified=True,
            data={"report": report},
        )

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
        return self.confirmations.get_pending(session_id)

    def _current_execution_id(self):
        current = self.execution_controller.current
        return current.get("execution_id", "") if isinstance(current, dict) else ""

    def _handle_format_method_request(
        self,
        request,
        session_id,
        original_command,
        log_callback=None,
        continuation=None,
    ):
        return self._runtime_services()._handle_format_method_request(
            request,
            session_id,
            original_command,
            log_callback=log_callback,
            continuation=continuation,
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

    def _resume_local_learned_dynamic(
        self, payload, confirmation_id, log_callback=None
    ):
        runtime = self._runtime_services()
        return self.learned_replay_service.resume_local_dynamic(
            runtime, payload, confirmation_id, log_callback=log_callback
        )

    def _skill_policy_preview_result(
        self, app_name, macro_name, skill, assessment
    ):
        return self.learned_replay_service.preview_result(
            app_name, macro_name, skill, assessment
        )

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
        return self.learned_replay_service.local_policy_gate(
            self._runtime_services(),
            app_name=app_name,
            macro_name=macro_name,
            skill=skill,
            slots=slots,
            argument=argument,
            session_id=session_id,
            original_command=original_command,
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
        runtime = self._runtime_services()
        return self.confirmations.resolve(
            runtime,
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
        return self.learned_replay_service.retry_step(
            self._runtime_services(), app_name, macro_name, step_number
        )

    def parse_and_execute(
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
        """Compatibility API returning only the user-facing message."""
        return self.execute_command_result(
            user_input, log_callback, image_data, mode, use_api, summary,
            stream_callback, conversation_state, session_id, edit_context,
        )["message"]

    def execute_command_result(
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
        """Execute one command and always return the canonical result object."""
        try:
            raw = self._parse_and_execute_core(
                user_input, log_callback, image_data, mode, use_api, summary,
                stream_callback, conversation_state, session_id, edit_context,
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
        edit_context=None,
    ):
        return self._runtime_services().execute_command_result(
            user_input, log_callback, image_data, mode, use_api, summary,
            stream_callback, conversation_state, session_id, edit_context,
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
            self._runtime_services(),
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
        return self.ai_action_handler.validate_result(
            self._runtime_services(), result
        )

    def _resolve_registered_app(self, target):
        return self.local_command_analyzer.resolve_registered_app(target)

    def _get_learned_macro(self, app_name, macro_name):
        return self.local_command_analyzer.get_learned_macro(
            app_name, macro_name
        )

    def _validate_generated_code(self, act, require_external_target=False):
        return self.ai_action_handler._validate_generated_code(
            self._runtime_services(),
            act,
            require_external_target=require_external_target,
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
