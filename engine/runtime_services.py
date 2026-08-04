"""Explicit runtime ports used below the :class:`CommandParser` facade.

The parser is the public compatibility surface.  Routing, confirmation, and
learned-skill layers must not receive that facade (or retain it indirectly).
This module builds a short-lived, explicit dependency bundle instead.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from engine.action_executor import (
    ActionPlanError,
    ActionPlanVerificationError,
)
from engine.execution_result import (
    failure_result,
    normalize_error_type,
    normalize_execution_result,
    success_result,
)
from engine.execution_runtime import ExecutionCancelled
from engine.macro_runner import MacroTimeoutError
from engine.parsing.office_command_parser import (
    EXCEL_FORMAT_METHODS,
    EXCEL_FORMAT_PREFERENCE_KEY,
)


@dataclass(slots=True)
class ParserRuntimeServices:
    """Dependencies needed while routing one parser request.

    The object deliberately contains no ``parser``/``owner`` field and no
    callback bound to ``CommandParser``.  It is rebuilt at public facade
    boundaries so tests and integrations that replace a dependency on the
    parser continue to work without hidden synchronization.
    """

    dict_mgr: Any
    llm_engine: Any
    template_matcher: Any
    command_pipeline: Any
    execution_controller: Any
    confirmations: Any
    ai_action_handler: Any
    app_action_registry: Any
    edit_mode_controller: Any
    edit_mode_handler: Any
    pdf_intake_manager: Any
    decision_engine: Any
    preference_manager: Any
    app_command_router: Any
    candidate_recording_service: Any
    skill_learning_service: Any
    learned_replay_service: Any
    action_executor: Any
    macro_runner: Any
    dynamic_code_preflight: Any
    builtins: Any
    skill_executor: Any
    skill_run_policy: Any
    local_command_analyzer: Any
    _handlers: dict[str, Any]

    @classmethod
    def from_facade(cls, facade: Any) -> "ParserRuntimeServices":
        """Copy only declared collaborators from the public facade."""
        return cls(**{
            field: getattr(facade, field)
            for field in cls.__dataclass_fields__
        })

    @property
    def pending_macros(self):
        return self.skill_learning_service.pending_macros

    @staticmethod
    def _latest_user_text(user_input):
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
        compact = re.sub(r"\s+", "", str(text or "").casefold())
        if not compact or any(
            marker in compact
            for marker in ("내일몇시", "어제몇시", "몇시간", "시간걸", "소요시간")
        ):
            return False
        return any(
            marker in compact
            for marker in (
                "지금몇시", "현재몇시", "몇시야", "몇시에요", "몇시인가",
                "지금시간", "현재시간", "시간알려줘", "시간몇인지",
            )
        ) and len(compact) <= 40

    @staticmethod
    def _is_current_weather_question(text):
        compact = re.sub(r"\s+", "", str(text or "").casefold())
        if "날씨" not in compact or len(compact) > 60:
            return False
        if any(
            marker in compact
            for marker in ("날씨란", "날씨자료", "과거날씨", "검색", "찾아")
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

    def analyze_command(self, user_input):
        return self.local_command_analyzer.analyze_command(user_input)

    def approve_pending_learning(self, edits=None):
        return self.skill_learning_service.approve(edits)

    def reject_pending_learning(self, reason="discard"):
        return self.skill_learning_service.reject(reason)

    def _diagnostic_manager(self):
        manager = getattr(self.execution_controller, "incident_manager", None)
        if manager is None:
            raise RuntimeError("자가 진단 저장소가 비활성화되어 있습니다.")
        return manager

    def diagnose_latest_failure(self):
        manager = self._diagnostic_manager()
        incident = manager.latest()
        if incident is None:
            return failure_result(
                "진단할 실패 기록이 없습니다.",
                action="self_diagnosis",
                error_type="target_not_found",
                status="not_found",
            )
        report = manager.report(incident["incident_id"])
        where = report["where"]
        failed_step = where.get("failed_step") or where.get("operation") or "불명확"
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
        preference = self.preference_manager.decision(EXCEL_FORMAT_PREFERENCE_KEY)
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
                log_callback(f"[Preference] {preferred_method} 선호를 자동 적용합니다.")
            result = self.app_command_router.execute(
                candidate,
                session_id,
                original_command,
                log_callback=log_callback,
                continuation=continuation,
                runtime=self,
            )
            if result.get("status") == "confirmation_required":
                return result
            if isinstance(continuation, dict) and continuation.get("kind") == "learned_native":
                return self.skill_executor.complete_native_confirmation(
                    continuation, result
                )
            return result
        return self.confirmations.queue_app_method(
            self,
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
        self.execution_controller.event(
            "dynamic_code_preflight",
            result.status,
            {
                "status": result.status,
                "action": context["action"],
                "app_name": context["app_name"],
                "macro_name": context["macro_name"],
                "code_sha256": result.code_sha256,
                "finding_codes": [item.code for item in result.findings],
            },
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
        return self.learned_replay_service.resume_local_dynamic(
            self, payload, confirmation_id, log_callback=log_callback
        )

    def _skill_policy_preview_result(
        self, app_name, macro_name, skill, assessment
    ):
        return self.learned_replay_service.preview_result(
            app_name, macro_name, skill, assessment
        )

    def _local_skill_policy_gate(self, **kwargs):
        return self.learned_replay_service.local_policy_gate(self, **kwargs)

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
        try:
            raw = self.command_pipeline.execute(
                self,
                user_input,
                log_callback=log_callback,
                image_data=image_data,
                mode=mode,
                use_api=use_api,
                summary=summary,
                stream_callback=stream_callback,
                conversation_state=conversation_state,
                session_id=session_id,
                edit_context=edit_context,
            )
            return normalize_execution_result(raw)
        except ExecutionCancelled as error:
            return failure_result(
                str(error), action="command", error_type="user_cancelled"
            )
        except Exception as error:
            return failure_result(
                str(error),
                action="command",
                error_type=self._failure_type_for_error(error),
                failed_step=getattr(error, "failed_step", None),
                retryable=getattr(error, "retryable", False),
            )

    def _execute_ai_action_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input_str,
        **options,
    ):
        return self.ai_action_handler.execute_batch(
            self,
            response,
            actions,
            validation_issues,
            user_input_str,
            **options,
        )

    @staticmethod
    def _build_dynamic_argument(learning, target, app_name):
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

    @staticmethod
    def _build_slot_values(
        learning,
        template_match=None,
        matched_app_name=None,
        matched_app_path=None,
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
            return str(
                learned_macro.get("default_target")
                or (matched_app_path if matched_app_path else (matched_app_name or ""))
            )
        values = self._build_slot_values(
            learning, template_match, matched_app_name, matched_app_path
        )
        values["_target"] = str(learned_macro.get("default_target", ""))
        values["_app"] = str(matched_app_name or learned_macro.get("app", ""))
        values["_app_path"] = str(matched_app_path or "")
        return json.dumps(values, ensure_ascii=False)

    def _resolve_registered_app(self, target):
        return self.local_command_analyzer.resolve_registered_app(target)

    def _get_learned_macro(self, app_name, macro_name):
        return self.local_command_analyzer.get_learned_macro(app_name, macro_name)

    def _record_native_candidate_success(
        self, app_name, learned_macro, *, source="learned_dynamic", execution_result=None
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

    def _candidate_manager(self):
        return self.candidate_recording_service.manager()

    def _candidate_execution_id(self):
        return self.candidate_recording_service.execution_id()

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
