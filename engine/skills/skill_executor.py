"""Common orchestration boundary for reusable learned skills."""

from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass

from engine.action_executor import ActionConfirmationRequired
from engine.action_registry import app_target_actions
from engine.execution_result import (
    failure_result,
    normalize_execution_result,
    normalize_error_type,
)
from engine.execution_runtime import ExecutionCancelled
from engine.security import BLOCKED, CONFIRMATION_REQUIRED, SAFE
from engine.ui_automation import UIAutomationAmbiguousTarget
from engine.skills.postconditions import FAILED, PostconditionEvaluator
from engine.skills.route_selector import (
    ROUTE_PLAN_KEYS,
    RouteSelector,
    SkillRouteUnavailable,
)
from engine.skills.skill_profile import SkillProfile
from engine.skills.run_policy import skill_policy_fingerprint


# Failures where nothing external changed yet, so one alternate route is safe.
FALLBACK_ALLOWED_CODES = frozenset({
    "route_unavailable",
    "target_not_found",
    "adapter_unavailable",
    "environment_unavailable",
    "connection_not_established",
})

# A returned (not raised) result carrying one of these is a real failure.
# Map the canonical command error taxonomy onto route-failure codes.
_ERROR_TYPE_TO_ROUTE_CODE = {
    "target_not_found": "target_not_found",
    "environment_error": "environment_unavailable",
}

_NATIVE_OPERATION_ALIASES = {
    ("excel", "set_cell_value"): "write_cell",
}


class SkillExecutionError(RuntimeError):
    error_type = "execution_error"
    status = "failed"


class SkillNotFoundError(SkillExecutionError):
    error_type = "target_not_found"
    route_failure_code = "target_not_found"
    state_changed = False


class SkillStateError(SkillExecutionError):
    error_type = "validation_error"


class SkillTargetContractError(SkillStateError):
    """A reusable structured plan points outside its approved app owner."""

    route_failure_code = "target_contract_mismatch"
    state_changed = False


class SkillNativeAppActionUnsupported(SkillStateError):
    """A stored native plan exceeds the single-command V1 replay contract."""

    route_failure_code = "app_action_requires_live_path"
    state_changed = False


class SkillContextChanged(SkillExecutionError):
    error_type = "validation_error"
    status = "context_changed"


class SkillPreflightBlocked(SkillExecutionError):
    error_type = "validation_error"
    status = "blocked"

    def __init__(self, preflight, route, fingerprint=""):
        super().__init__("동적 Python 코드를 안전 정책에서 차단했습니다.")
        self.preflight = preflight
        self.route = route
        self.fingerprint = fingerprint


class SkillConfirmationRequired(SkillExecutionError):
    error_type = "validation_error"
    status = "confirmation_required"

    def __init__(self, preflight, route, fingerprint):
        super().__init__("동적 Python 코드 실행 전 사용자 확인이 필요합니다.")
        self.preflight = preflight
        self.route = route
        self.fingerprint = fingerprint


class SkillReturnedFailure(SkillExecutionError):
    """Executor returned a failure result instead of raising (stage 7-0 B)."""

    def __init__(self, result, route):
        message = str(result.get("message") or "실행이 실패로 반환되었습니다.")
        super().__init__(message)
        self.result = result
        self.route = route
        self.error_type = normalize_error_type(
            result.get("error_type") or "execution_error"
        )
        self.status = str(result.get("status") or "failed")
        self.failed_step = result.get("failed_step")
        # A returned failure means the executor already ran; assume the world
        # may have changed and never auto-retry on top of it.
        self.state_changed = True


class SkillVerificationFailed(SkillExecutionError):
    """The route completed, but its explicit final-state checks failed."""

    error_type = "verification_error"
    status = "verification_failed"
    route_failure_code = "verification_failed"
    state_changed = True

    def __init__(self, summary, route):
        super().__init__(
            "실행은 끝났지만 요청한 결과의 사후조건 검증에 실패했습니다."
        )
        self.result = summary.result
        self.route = route
        self.checks = [dict(item) for item in summary.checks]
        self.rollback_performed = bool(
            summary.result.get("rollback_performed", False)
        )
        # None when no rollback ran, so "not attempted" stays distinct from
        # "attempted and failed" for later log readers.
        self.rollback_success = (
            bool(summary.result.get("rollback_success", False))
            if self.rollback_performed else None
        )


# Exceptions that must bubble unchanged and never trigger a fallback route.
_NEVER_FALLBACK = (
    SkillConfirmationRequired,
    SkillPreflightBlocked,
    SkillContextChanged,
    ActionConfirmationRequired,
    UIAutomationAmbiguousTarget,
    ExecutionCancelled,
)


@dataclass(frozen=True)
class SkillPreflightDecision:
    route: str
    status: str
    result: object | None = None
    fingerprint: str = ""


class SkillExecutor:
    """Load, select, preflight, execute, verify, and record one skill route."""

    def __init__(self, owner, route_selector=None, postconditions=None):
        self.owner = owner
        self.route_selector = route_selector or RouteSelector()
        self.postconditions = postconditions or PostconditionEvaluator(owner)

    @property
    def dict_mgr(self):
        return self.owner.dict_mgr

    def load(self, app_name, macro_name):
        learned = getattr(self.dict_mgr, "learned_macros", {}).get(app_name, {})
        skill = learned.get(macro_name) if isinstance(learned, dict) else None
        if not isinstance(skill, dict):
            raise SkillNotFoundError(
                f"학습된 매크로({app_name}/{macro_name})를 찾을 수 없습니다."
            )
        return skill

    @staticmethod
    def _ensure_active(skill, app_name, macro_name):
        state = str(skill.get("state", "active") or "active")
        if state != "active":
            raise SkillStateError(
                f"학습 행동({app_name}/{macro_name})이 {state} 상태라 실행할 수 없습니다."
            )

    def _selection(self, skill, *, code_override=None):
        profile = SkillProfile.from_skill(
            skill,
            route_override="python" if code_override is not None else None,
        )
        return self.route_selector.select(
            skill, profile=profile, code_override=code_override
        )

    @staticmethod
    def _plan_for_route(skill, route):
        plan_key = ROUTE_PLAN_KEYS.get(route)
        return skill.get(plan_key) if plan_key else None

    def preflight(
        self,
        app_name,
        macro_name,
        *,
        skill=None,
        code_override=None,
        argument="",
        action="use_learned_macro",
        target="",
        log_callback=None,
    ):
        skill = skill if isinstance(skill, dict) else self.load(app_name, macro_name)
        self._ensure_active(skill, app_name, macro_name)
        profile = SkillProfile.from_skill(
            skill,
            route_override="python" if code_override is not None else None,
        )
        selection = self.route_selector.select(
            skill, profile=profile, code_override=code_override
        )
        if selection.route != "python":
            return SkillPreflightDecision(selection.route, SAFE)

        code = skill.get("code", "") if code_override is None else code_override
        result, fingerprint = self.owner._analyze_dynamic_code(
            code,
            argument,
            action=action,
            app_name=app_name,
            macro_name=macro_name,
            target=target or skill.get("default_target", ""),
            log_callback=log_callback,
        )
        return SkillPreflightDecision(
            selection.route,
            result.status,
            result=result,
            fingerprint=fingerprint,
        )

    # ------------------------------------------------------------------
    # Fallback classification helpers
    # ------------------------------------------------------------------
    def _route_failure_code(self, error):
        code = getattr(error, "route_failure_code", None)
        if code:
            return str(code)
        failure_type = self.owner._failure_type_for_error(error)
        return _ERROR_TYPE_TO_ROUTE_CODE.get(failure_type, failure_type)

    @staticmethod
    def _state_changed(error):
        if hasattr(error, "state_changed"):
            return bool(getattr(error, "state_changed"))
        # ActionExecutor tags the failing step once the mutation loop starts and
        # only then; a preflight/validation failure leaves both markers empty.
        if getattr(error, "failed_step", None) is not None:
            return True
        return bool(getattr(error, "completed", None))

    def _can_fallback(self, *, has_next, failure_code, state_changed, attempts_done):
        return (
            has_next
            and attempts_done < 1
            and not state_changed
            and failure_code in FALLBACK_ALLOWED_CODES
        )

    # ------------------------------------------------------------------
    # Route runners
    # ------------------------------------------------------------------
    def _run_route(self, route, skill, params, diagnostic):
        if route == "python":
            return self._run_python_route(skill, params, diagnostic)
        return self._run_plan_route(route, skill, params, diagnostic)

    @staticmethod
    def _app_command_steps(plan):
        if not isinstance(plan, (list, tuple)):
            return []
        return [
            index
            for index, step in enumerate(plan, start=1)
            if isinstance(step, dict) and step.get("action") == "app_command"
        ]

    @staticmethod
    def _normalize_native_plan(route, plan):
        """Convert the stored native template schema to runtime plan fields."""
        if route != "native" or not isinstance(plan, list):
            return plan
        normalized = []
        for step in plan:
            if not isinstance(step, dict):
                normalized.append(step)
                continue
            item = copy.deepcopy(step)
            if item.get("target_app") and (
                not item.get("action") or item.get("action") == "app_command"
            ):
                item.setdefault("action", "app_command")
                item.setdefault("target", item.get("target_app"))
                item.pop("target_app", None)
            normalized.append(item)
        return normalized

    @staticmethod
    def _canonical_app(value):
        compact = re.sub(r"[^0-9a-zA-Z가-힣]+", "", str(value or "")).casefold()
        aliases = {
            "excel": "excel", "엑셀": "excel",
            "hwp": "hwp", "한글": "hwp", "한컴오피스한글": "hwp",
            "word": "word", "워드": "word", "microsoftword": "word",
            "ppt": "powerpoint", "파워포인트": "powerpoint",
            "powerpoint": "powerpoint", "microsoftpowerpoint": "powerpoint",
            "system": "system", "시스템": "system",
            "windows": "system", "윈도우": "system",
        }
        return aliases.get(compact, compact)

    @classmethod
    def _validate_plan_target_contract(cls, app_name, plan):
        """Bind a stored app-specific plan to that app before any side effect."""
        owner_app = cls._canonical_app(app_name)
        target_actions = set(app_target_actions()) | {
            "hotkey", "type_text", "app_command",
        }
        targets = []
        for step in plan if isinstance(plan, list) else []:
            if not isinstance(step, dict) or step.get("action") not in target_actions:
                continue
            target = cls._canonical_app(step.get("target"))
            if target and target not in targets:
                targets.append(target)
        if owner_app and owner_app != "system":
            mismatches = [target for target in targets if target != owner_app]
            if mismatches:
                raise SkillTargetContractError(
                    "학습 행동의 등록 앱과 실제 계획 대상 앱이 달라 실행하지 않았습니다. "
                    "학습 목록에서 이 행동을 다시 검토해주세요."
                )
        return {
            "owner_app": owner_app,
            "target_apps": targets,
            "validated": True,
            "multi_app_allowed": owner_app == "system",
        }

    def _native_continuation(self, route, skill, params, diagnostic):
        return {
            "kind": "learned_native",
            "app_name": params["app_name"],
            "macro_name": params["macro_name"],
            "skill_fingerprint": skill_policy_fingerprint(skill),
            "route": route,
            "slots": copy.deepcopy(dict(params["slots"] or {})),
            "source": params["source"],
            "record_usage": bool(params["record_usage"]),
            "record_candidate": bool(params["record_candidate"]),
            "verification_required": bool(params["verification_required"]),
            "started_monotonic": params["started_monotonic"],
            "diagnostic": copy.deepcopy(diagnostic),
        }

    def _run_native_app_command(self, route, plan, skill, params, diagnostic):
        if len(plan) != 1 or self._app_command_steps(plan) != [1]:
            raise SkillNativeAppActionUnsupported(
                "V1 학습 네이티브 실행은 Excel 또는 한글 app_command 한 단계만 지원합니다."
            )
        rendered = self.owner.action_executor.render_plan(
            plan, dict(params["slots"] or {})
        )
        step = rendered[0]
        target = str(step.get("target") or "").strip().casefold()
        if target not in {"excel", "hwp"}:
            raise SkillNativeAppActionUnsupported(
                "V1 학습 네이티브 실행 대상은 Excel 또는 한글만 지원합니다."
            )
        request = {
            "action": "app_command",
            "target": target,
            "operation": _NATIVE_OPERATION_ALIASES.get(
                (target, step.get("operation", "")),
                step.get("operation", ""),
            ),
            "params": copy.deepcopy(step.get("params", {})),
        }
        return self.owner.app_command_router.execute(
            request,
            params["session_id"],
            params["original_command"],
            log_callback=params["log_callback"],
            continuation=self._native_continuation(
                route, skill, params, diagnostic
            ),
        )

    def _run_plan_route(self, route, skill, params, diagnostic):
        plan = self._plan_for_route(skill, route)
        if not plan:
            raise SkillRouteUnavailable(
                f"스킬 실행 경로 '{route}'에 필요한 실행 데이터가 없습니다."
            )
        plan = self._normalize_native_plan(route, plan)
        rendered = self.owner.action_executor.render_plan(
            plan, dict(params["slots"] or {})
        )
        diagnostic["target_contract"] = self._validate_plan_target_contract(
            params["app_name"], rendered
        )
        if self._app_command_steps(rendered):
            return self._run_native_app_command(
                route, rendered, skill, params, diagnostic
            )
        return self.owner.action_executor.execute_plan(
            plan,
            dict(params["slots"] or {}),
            params["log_callback"],
            start_step=params["start_step"],
            retry_attempts=params["retry_attempts"],
        )

    def _run_python_route(self, skill, params, diagnostic):
        code_override = params["code_override"]
        code = skill.get("code", "") if code_override is None else code_override
        if not str(code).strip():
            raise SkillRouteUnavailable(
                "스킬 실행 경로 'python'에 필요한 실행 코드가 없습니다."
            )
        decision = self.preflight(
            params["app_name"],
            params["macro_name"],
            skill=skill,
            code_override=code_override,
            argument=params["argument"],
            action=params["action"],
            target=params["target"],
            log_callback=params["log_callback"],
        )
        diagnostic["preflight_status"] = decision.status
        expected = params["expected_code_sha256"]
        if expected and decision.result.code_sha256 != expected:
            self.owner.execution_controller.event(
                "skill_executor", "context_changed", dict(diagnostic)
            )
            raise SkillContextChanged(
                "확인 후 저장된 스킬 코드가 변경되어 실행하지 않았습니다."
            )
        if decision.status == BLOCKED:
            self.owner.execution_controller.event(
                "skill_executor", "blocked", dict(diagnostic)
            )
            raise SkillPreflightBlocked(
                decision.result, "python", decision.fingerprint
            )
        approved = set(params["approved_fingerprints"] or [])
        if (
            decision.status == CONFIRMATION_REQUIRED
            and decision.fingerprint not in approved
        ):
            self.owner.execution_controller.event(
                "skill_executor", "confirmation_required", dict(diagnostic)
            )
            raise SkillConfirmationRequired(
                decision.result, "python", decision.fingerprint
            )
        return self.owner.macro_runner.run(code, params["argument"])

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------
    def _record_success(self, params, route, skill, result):
        if params["record_usage"]:
            self.dict_mgr.record_learned_macro_result(
                params["app_name"], params["macro_name"], True
            )
            self.dict_mgr.record_learned_macro_verification(
                params["app_name"], params["macro_name"],
                bool(result.get("verified", False)),
            )
        # Only actual dynamic Python work counts toward a native candidate.
        if params["record_candidate"] and route == "python":
            self.owner.candidate_recording_service.record_success(
                params["app_name"],
                skill,
                source=params["source"],
                execution_result=result,
                selected_route=route,
                slots=params.get("slots"),
                argument=params.get("argument", ""),
            )

    def _record_failure(self, params, route, skill, failure_type):
        if params["record_usage"]:
            self.dict_mgr.record_learned_macro_result(
                params["app_name"], params["macro_name"], False, failure_type
            )
        if params["record_candidate"] and route == "python":
            self.owner.candidate_recording_service.record_failure(
                params["app_name"], skill, source=params["source"],
                selected_route=route,
            )

    def execute(
        self,
        app_name,
        macro_name,
        *,
        skill=None,
        code_override=None,
        argument="",
        slots=None,
        source="learned_dynamic",
        action="use_learned_macro",
        target="",
        approved_fingerprints=None,
        expected_code_sha256="",
        log_callback=None,
        start_step=1,
        retry_attempts=None,
        record_usage=True,
        record_candidate=True,
        session_id=None,
        original_command="",
    ):
        skill = skill if isinstance(skill, dict) else self.load(app_name, macro_name)
        self._ensure_active(skill, app_name, macro_name)
        profile = SkillProfile.from_skill(
            skill,
            route_override="python" if code_override is not None else None,
        )
        selection = self.route_selector.select(
            skill, profile=profile, code_override=code_override
        )
        chain = list(selection.attempt_chain)
        started = time.monotonic()
        params = {
            "app_name": app_name,
            "macro_name": macro_name,
            "source": source,
            "argument": argument,
            "slots": slots,
            "action": action,
            "target": target or skill.get("default_target", ""),
            "code_override": code_override,
            "approved_fingerprints": approved_fingerprints,
            "expected_code_sha256": expected_code_sha256,
            "log_callback": log_callback,
            "start_step": start_step,
            "retry_attempts": retry_attempts,
            "record_usage": record_usage,
            "record_candidate": record_candidate,
            "verification_required": profile.verification_required,
            "session_id": session_id,
            "original_command": (
                original_command or f"{app_name}/{macro_name} 학습 행동 실행"
            ),
            "started_monotonic": started,
        }
        diagnostic = {
            "app_name": str(app_name or "")[:100],
            "macro_name": str(macro_name or "")[:100],
            "source": str(source or "learned_dynamic")[:80],
            "selected_route": selection.route,
            "primary_route": selection.primary_route,
            "route_selection_reason": selection.reason,
            "attempted_routes": [],
            "route_attempts": [],
            "available_routes": list(selection.available_routes),
            "fallback_used": False,
            "fallback_reason": "",
            "fallback_attempts": 0,
            "state_changed_before_failure": False,
            "rollback_performed": False,
            # None until a rollback actually runs; True/False only then.
            "rollback_success": None,
            "preflight_status": SAFE,
        }
        self.owner.execution_controller.event(
            "skill_executor", "route_selected", dict(diagnostic)
        )
        if log_callback:
            log_callback(
                f"[SkillExecutor] {app_name}/{macro_name} 경로 선택: "
                f"{selection.route}"
            )

        last_error = None
        for index, route in enumerate(chain):
            is_fallback = index > 0
            diagnostic["selected_route"] = route
            diagnostic["attempted_routes"].append(route)
            if is_fallback:
                diagnostic["fallback_used"] = True
                diagnostic["fallback_attempts"] += 1
                if log_callback:
                    log_callback(
                        f"[SkillExecutor] 대체 경로 시도({route}): "
                        f"{diagnostic['fallback_reason']}"
                    )

            try:
                raw_result = self._run_route(route, skill, params, diagnostic)
                normalized = normalize_execution_result(raw_result, action=route)
                if normalized.get("status") == "confirmation_required":
                    diagnostic["route_attempts"].append({
                        "route": route,
                        "status": "confirmation_required",
                        "verified": False,
                    })
                    pending_diagnostic = dict(diagnostic)
                    data = dict(normalized.get("data") or {})
                    data["skill_execution"] = pending_diagnostic
                    normalized["data"] = data
                    normalized["skill_execution"] = pending_diagnostic
                    self.owner.execution_controller.event(
                        "skill_executor",
                        "confirmation_required",
                        pending_diagnostic,
                    )
                    return normalized
                result = self._finish_success(
                    route, skill, params, diagnostic, raw_result, started
                )
                return result
            except _NEVER_FALLBACK as error:
                self._handle_never_fallback(error, params, diagnostic)
                raise
            except Exception as error:  # noqa: BLE001 - classified below
                failure_code = self._route_failure_code(error)
                state_changed = self._state_changed(error)
                diagnostic["route_attempts"].append({
                    "route": route,
                    "status": "failed",
                    "failure_code": failure_code,
                    "state_changed": state_changed,
                })
                if state_changed:
                    diagnostic["state_changed_before_failure"] = True
                if hasattr(error, "rollback_performed"):
                    performed = bool(getattr(error, "rollback_performed"))
                    diagnostic["rollback_performed"] = performed
                    diagnostic["rollback_success"] = (
                        bool(getattr(error, "rollback_success", False))
                        if performed else None
                    )
                last_error = error
                if self._can_fallback(
                    has_next=index + 1 < len(chain),
                    failure_code=failure_code,
                    state_changed=state_changed,
                    attempts_done=diagnostic["fallback_attempts"],
                ):
                    diagnostic["fallback_reason"] = failure_code
                    continue
                self._finish_failure(
                    route, skill, params, diagnostic, error, started
                )
                raise

        # The chain is always non-empty; reaching here means every attempt
        # raised without qualifying for fallback and re-raised above.
        raise last_error  # pragma: no cover - defensive

    def validate_native_continuation(self, continuation):
        """Fail closed if a queued learned-native plan changed while waiting."""
        continuation = (
            continuation if isinstance(continuation, dict) else {}
        )
        app_name = continuation.get("app_name")
        macro_name = continuation.get("macro_name")
        try:
            skill = self.load(app_name, macro_name)
        except SkillNotFoundError as error:
            return None, failure_result(
                str(error),
                action="learned_macro",
                target=macro_name,
                error_type="target_not_found",
                status="context_changed",
            )
        if skill_policy_fingerprint(skill) != continuation.get(
            "skill_fingerprint"
        ):
            return None, failure_result(
                "확인하는 동안 학습 행동의 네이티브 계획이 바뀌어 실행하지 않았습니다.",
                action="learned_macro",
                target=macro_name,
                error_type="validation_error",
                status="context_changed",
            )
        return skill, None

    def complete_native_confirmation(self, continuation, raw_result):
        """Finish verification and usage accounting after native approval."""
        continuation = (
            continuation if isinstance(continuation, dict) else {}
        )
        skill, validation_error = self.validate_native_continuation(
            continuation
        )
        if validation_error is not None:
            return validation_error
        app_name = continuation.get("app_name")
        macro_name = continuation.get("macro_name")

        route = continuation.get("route") or "native"
        diagnostic = copy.deepcopy(continuation.get("diagnostic") or {})
        params = {
            "app_name": app_name,
            "macro_name": macro_name,
            "source": continuation.get("source", "confirmation_resume"),
            "slots": copy.deepcopy(continuation.get("slots") or {}),
            "record_usage": bool(continuation.get("record_usage", True)),
            "record_candidate": bool(
                continuation.get("record_candidate", False)
            ),
            "verification_required": bool(
                continuation.get("verification_required", False)
            ),
        }
        started = continuation.get("started_monotonic")
        if not isinstance(started, (int, float)):
            started = time.monotonic()
        try:
            return self._finish_success(
                route, skill, params, diagnostic, raw_result, started
            )
        except Exception as error:  # converted to command result boundary
            self._finish_failure(
                route, skill, params, diagnostic, error, started
            )
            existing = getattr(error, "result", None)
            if isinstance(existing, dict):
                return existing
            return failure_result(
                str(error),
                action="learned_macro",
                target=macro_name,
                error_type=self.owner._failure_type_for_error(error),
                status=getattr(error, "status", "failed"),
                data={"execution_result": raw_result},
            )

    def _finish_success(self, route, skill, params, diagnostic, raw_result, started):
        normalized = normalize_execution_result(raw_result, action=route)
        status = str(normalized.get("status") or "")
        # Stage 7-0 B: any explicit success=false is never a successful run,
        # including confirmation_required and future failure statuses.
        if normalized.get("success") is False:
            raise SkillReturnedFailure(normalized, route)

        postcondition = self.postconditions.evaluate(
            raw_result,
            route=route,
            conditions=skill.get("postconditions", []),
            slots=params["slots"],
            verification_required=params["verification_required"],
        )
        if postcondition.verification_status == FAILED:
            raise SkillVerificationFailed(postcondition, route)
        result = postcondition.result
        diagnostic["route_attempts"].append({
            "route": route,
            "status": "success",
            "verified": postcondition.verified,
        })
        diagnostic.update({
            "verified": postcondition.verified,
            "verification_status": postcondition.verification_status,
            "process_success": postcondition.process_success,
            "postcondition_checks": [
                dict(item) for item in postcondition.checks
            ],
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        })
        data = dict(result.get("data") or {})
        data["skill_execution"] = dict(diagnostic)
        result["data"] = data
        result["skill_execution"] = dict(diagnostic)

        self._record_success(params, route, skill, result)
        self.owner.execution_controller.event(
            "skill_executor", "success", dict(diagnostic)
        )
        return result

    def _finish_failure(self, route, skill, params, diagnostic, error, started):
        failure_type = self.owner._failure_type_for_error(error)
        diagnostic.update({
            "error_type": failure_type,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        })
        try:
            error.skill_execution = dict(diagnostic)
            result = getattr(error, "result", None)
            if isinstance(result, dict):
                data = dict(result.get("data") or {})
                data["skill_execution"] = dict(diagnostic)
                result["data"] = data
        except Exception:
            pass
        self._record_failure(params, route, skill, failure_type)
        self.owner.execution_controller.event(
            "skill_executor", "failed", dict(diagnostic)
        )

    def _handle_never_fallback(self, error, params, diagnostic):
        try:
            error.skill_execution = dict(diagnostic)
        except Exception:
            pass
        if isinstance(error, ExecutionCancelled):
            if params["record_usage"]:
                self.dict_mgr.record_learned_macro_result(
                    params["app_name"], params["macro_name"], False, "user_cancelled"
                )
            self.owner.execution_controller.event(
                "skill_executor", "cancelled", dict(diagnostic)
            )
        elif isinstance(error, ActionConfirmationRequired):
            self.owner.execution_controller.event(
                "skill_executor", "confirmation_required", dict(diagnostic)
            )
        elif isinstance(error, UIAutomationAmbiguousTarget):
            self.owner.execution_controller.event(
                "skill_executor", "confirmation_required", dict(diagnostic)
            )
        # SkillConfirmationRequired / SkillPreflightBlocked / SkillContextChanged
        # already emitted their own event inside the python route runner.
