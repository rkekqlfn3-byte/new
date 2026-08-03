import copy

from engine.app_actions import (
    AppActionAmbiguousTarget,
    AppActionError,
    PreparedAction,
)
from engine.execution_result import failure_result
from engine.managers.pending_confirmation_manager import normalize_session_id
from engine.parsing.office_command_parser import (
    EXCEL_FORMAT_METHODS,
    EXCEL_FORMAT_PREFERENCE_KEY,
)
from engine.skills.run_policy import skill_policy_fingerprint


def _current_execution_id(execution_controller):
    current = execution_controller.current
    return current.get("execution_id", "") if isinstance(current, dict) else ""


class ConfirmationFactory:
    """Build confirmation records while the parser keeps compatibility façades."""

    def __init__(self, pending_manager, response_handler):
        self.manager = pending_manager
        self.response_handler = response_handler

    def queue_missing_information(
        self,
        runtime,
        request,
        session_id,
        original_command,
        log_callback=None,
    ):
        params = request.get("params", {}) if isinstance(request, dict) else {}
        reason = str(params.get("reason") or "unsafe_default")
        message = str(params.get("message") or "필요한 정보를 조금 더 알려주세요.")
        try:
            context_identity = runtime.app_command_router.context_identity(
                request.get("target")
            )
        except AppActionError as error:
            return runtime.app_command_router.failure(error)
        if log_callback:
            log_callback(f"[Clarification] 추가 정보 필요: {reason}")
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or "",
            original_command=original_command,
            reason=reason,
            request_kind="clarification",
            message=message,
            action="clarification",
            target=str(request.get("target") or "")[:80],
            options=[
                {
                    "id": "provide_details",
                    "label": "내용 입력",
                    "description": "채팅 입력창에 빠진 정보를 포함한 문장을 입력합니다.",
                    "input_only": True,
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "아무 작업도 실행하지 않습니다.",
                    "cancel": True,
                    "aliases": ["취소", "그만", "하지마", "아니요"],
                },
            ],
            payload={
                "kind": "clarification_rephrase",
                "accept_free_text": True,
                "reason": reason,
                "target": str(request.get("target") or "")[:80],
                "context_identity": context_identity,
            },
        )
        return self.response_handler.confirmation_result(record)

    def queue_command_macro(
        self, runtime, macro_name, macro_data, original_command, session_id
    ):
        command = str(macro_data.get("data", "")).strip()
        try:
            runtime.builtins.validate_command(command)
        except (TypeError, ValueError) as error:
            return failure_result(
                str(error),
                action="command_line",
                target=command or macro_name,
                error_type="validation_error",
                status="blocked",
            )
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller),
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
        return self.response_handler.confirmation_result(record)

    def queue_app_method(
        self,
        runtime,
        request,
        session_id,
        original_command,
        log_callback=None,
        continuation=None,
    ):
        prepared_actions = {}
        requests = {}
        preparation_errors = []
        for option_id, operation in EXCEL_FORMAT_METHODS.items():
            try:
                candidate = copy.deepcopy(request)
                candidate["operation"] = operation
                prepared = runtime.app_command_router.prepare(
                    candidate.get("target"), operation, candidate.get("params", {})
                )
                prepared_actions[option_id] = prepared.to_dict()
                requests[option_id] = candidate
            except AppActionAmbiguousTarget as error:
                return self.queue_app_target(
                    runtime,
                    request,
                    error,
                    session_id,
                    original_command,
                    continuation=continuation,
                )
            except AppActionError as error:
                preparation_errors.append(error)

        if not prepared_actions:
            return runtime.app_command_router.failure(preparation_errors[0])

        representative = PreparedAction.from_dict(
            next(iter(prepared_actions.values()))
        )
        preference = runtime.preference_manager.decision(
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
                f"[Clarification] Excel {representative.sheet}!"
                f"{representative.target} 표시 방식 선택 필요"
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
        options = [
            {
                "id": option_id,
                **option_templates[option_id],
                "recommended": option_id == preferred_option,
            }
            for option_id in EXCEL_FORMAT_METHODS
            if option_id in prepared_actions
        ]
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
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or "",
            original_command=original_command,
            reason="multiple_possible_intents",
            request_kind="clarification",
            message=(
                "값이 바뀔 때 색상도 자동으로 바뀌게 할까요? "
                f"대상: {representative.workbook_name} / {representative.sheet} / "
                f"{representative.target}.{disabled_note}"
            ),
            action="app_command",
            target=(
                f"{representative.workbook_name}/"
                f"{representative.sheet}/{representative.target}"
            ),
            options=options,
            rememberable=True,
            payload={
                "kind": "app_method_choice",
                "requests": requests,
                "prepared_actions": prepared_actions,
                "preference_key": EXCEL_FORMAT_PREFERENCE_KEY,
                "continuation": copy.deepcopy(continuation),
            },
        )
        return self.response_handler.confirmation_result(record)

    def queue_hwp_scope(
        self,
        runtime,
        request,
        session_id,
        original_command,
        log_callback=None,
        continuation=None,
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
            },
        }
        for scope in ("selection", "document"):
            candidate = copy.deepcopy(request)
            candidate["operation"] = "find_replace"
            candidate["params"]["scope"] = scope
            try:
                prepared = runtime.app_command_router.prepare(
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
            return runtime.app_command_router.failure(errors[0])
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
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or "",
            original_command=original_command,
            reason="missing_range",
            request_kind="clarification",
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
                "continuation": copy.deepcopy(continuation),
            },
        )
        return self.response_handler.confirmation_result(record)

    def queue_app_target(
        self,
        runtime,
        request,
        ambiguity,
        session_id,
        original_command,
        continuation=None,
    ):
        candidate_requests = {}
        candidate_prepared = {}
        options = []
        operation = request.get("operation")
        if operation == "choose_format_method":
            operation = "apply_conditional_format"
        try:
            for index, column in enumerate(ambiguity.candidates):
                option_id = f"column_{column.lower()}"
                candidate = copy.deepcopy(request)
                candidate["params"].pop("source_range", None)
                candidate["params"]["column_name"] = column
                prepared = runtime.app_command_router.prepare(
                    candidate.get("target"), operation, candidate.get("params", {})
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
            return runtime.app_command_router.failure(error)
        options.append({
            "id": "cancel",
            "label": "취소",
            "description": "Excel을 변경하지 않습니다.",
            "cancel": True,
            "aliases": ["아니", "아니요", "그만", "하지마"],
        })
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or "",
            original_command=original_command,
            reason="ambiguous_reference",
            request_kind="clarification",
            message=str(ambiguity),
            action="app_command",
            target=ambiguity.target_name or "excel_column",
            options=options,
            payload={
                "kind": "app_target_choice",
                "requests": candidate_requests,
                "prepared_actions": candidate_prepared,
                "continuation": copy.deepcopy(continuation),
            },
        )
        return self.response_handler.confirmation_result(record)

    def queue_dynamic_code(
        self,
        runtime,
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
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller),
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
        return self.response_handler.confirmation_result(record)

    def queue_local_learned_dynamic(
        self,
        runtime,
        app_name,
        macro_name,
        argument,
        result,
        fingerprint,
        session_id,
        original_command,
    ):
        return self.queue_dynamic_code(
            runtime,
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

    def queue_skill_run_policy(
        self,
        runtime,
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
                "aliases": ["앞으로 자동", "항상 자동", "자동 실행", "묻지 마"],
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
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or "",
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
        return self.response_handler.confirmation_result(record)

    def queue_uia_target(
        self, runtime, ambiguity, payload, session_id, original_command
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
                "aliases": [str(index), f"{index}번", f"{index}번째", name],
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
        record = self.manager.create(
            session_id=normalize_session_id(session_id),
            execution_id=_current_execution_id(runtime.execution_controller) or payload.get(
                "execution_id", ""
            ),
            original_command=original_command,
            reason="ambiguous_reference",
            request_kind="clarification",
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
        return self.response_handler.confirmation_result(record)

    def queue_learned_uia_target(
        self,
        runtime,
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
        return self.queue_uia_target(
            runtime,
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
