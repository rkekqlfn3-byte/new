"""Validation, preparation, and execution services for Stage 10 actions."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from pathlib import Path

from engine.edit_mode.contracts import EditPreparedAction, RiskLevel
from engine.workflows import (
    WorkflowJoinValidationError,
    WorkflowSourceScopeValidationError,
)
from engine.workflows.business_workflow import file_fingerprint


class RelationshipInspectionValidator:
    """Validate the content-free schema returned by Excel relationship scans."""

    TOP_LEVEL_FIELDS = {
        "status",
        "candidate_count",
        "candidates",
        "automatic_execution_allowed",
        "raw_cell_values_stored",
        "document_paths_reported",
    }

    def __init__(self, error_type, candidate_fields):
        self.error_type = error_type
        self.candidate_fields = candidate_fields

    def validate(self, value):
        if not isinstance(value, Mapping) or set(value) != self.TOP_LEVEL_FIELDS:
            raise self.error_type("시트 관계 후보 검사 결과 형식이 올바르지 않습니다.")
        raw = value.get("candidates")
        if not isinstance(raw, list) or len(raw) > 10:
            raise self.error_type("시트 관계 후보는 최대 10개여야 합니다.")
        count = value.get("candidate_count")
        if not isinstance(count, int) or isinstance(count, bool):
            raise self.error_type("시트 관계 후보 개수 형식이 올바르지 않습니다.")
        if value.get("automatic_execution_allowed") is not False:
            raise self.error_type("키 후보 검사만으로 조인을 자동 실행할 수 없습니다.")
        if value.get("raw_cell_values_stored") is not False or value.get("document_paths_reported") is not False:
            raise self.error_type("키 후보 결과에는 셀 값이나 파일 경로를 넣을 수 없습니다.")
        candidates = []
        identities = set()
        for item in raw:
            candidate, identity = self._candidate(item)
            if identity in identities:
                raise self.error_type("같은 시트 관계 후보를 중복 표시할 수 없습니다.")
            identities.add(identity)
            candidates.append(candidate)
        status = str(value.get("status") or "")
        expected = "candidate_found" if candidates else "no_candidate"
        if status != expected or count != len(candidates):
            raise self.error_type("시트 관계 후보 개수 검증에 실패했습니다.")
        return {
            "status": status,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "automatic_execution_allowed": False,
            "raw_cell_values_stored": False,
            "document_paths_reported": False,
        }

    def _candidate(self, value):
        if not isinstance(value, Mapping) or set(value) != self.candidate_fields:
            raise self.error_type("시트 관계 후보 항목이 불완전합니다.")
        candidate = dict(value)
        for name in ("left_sheet", "right_sheet", "left_key", "right_key"):
            candidate[name] = self._name(candidate.get(name))
        if candidate["left_sheet"].casefold() == candidate["right_sheet"].casefold():
            raise self.error_type("서로 다른 두 시트의 관계 후보만 허용합니다.")
        left_key = self._key(candidate["left_key"])
        right_key = self._key(candidate["right_key"])
        self._validate_basis(candidate, left_key, right_key)
        self._validate_counts(candidate)
        self._validate_coverage(candidate)
        self._validate_confidence(candidate)
        identity = tuple(sorted((
            (candidate["left_sheet"].casefold(), left_key),
            (candidate["right_sheet"].casefold(), right_key),
        )))
        return candidate, identity

    def _name(self, value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or len(text) > 80 or any(ord(character) < 32 for character in text):
            raise self.error_type("시트 관계 후보 이름이 올바르지 않습니다.")
        return text

    @staticmethod
    def _key(value):
        return re.sub(r"[\s_\-]+", "", value).casefold()

    @staticmethod
    def _key_like(key):
        return key in {"id", "key", "no", "번호", "코드", "식별자"} or key.endswith(
            ("id", "key", "번호", "코드")
        )

    def _validate_basis(self, candidate, left_key, right_key):
        basis = str(candidate.get("match_basis") or "")
        if basis == "normalized_header" and left_key != right_key:
            raise self.error_type("같은 이름 후보의 양쪽 키 이름이 일치하지 않습니다.")
        if basis == "value_overlap" and (
            left_key == right_key
            or not (self._key_like(left_key) and self._key_like(right_key))
        ):
            raise self.error_type("이름이 다른 후보는 양쪽 모두 ID·코드·번호형 키여야 합니다.")
        if basis not in {"normalized_header", "value_overlap"}:
            raise self.error_type("시트 관계 후보 근거가 올바르지 않습니다.")
        candidate["match_basis"] = basis
        if not isinstance(candidate.get("ambiguous"), bool):
            raise self.error_type("시트 관계 후보 모호성 상태가 올바르지 않습니다.")
        if basis == "normalized_header" and candidate["ambiguous"]:
            raise self.error_type("같은 이름 후보를 모호한 후보로 표시할 수 없습니다.")

    def _validate_counts(self, candidate):
        cardinality = str(candidate.get("cardinality") or "")
        if cardinality not in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}:
            raise self.error_type("시트 관계 형태가 올바르지 않습니다.")
        candidate["cardinality"] = cardinality
        for name in ("matched_key_count", "left_distinct_count", "right_distinct_count"):
            number = candidate.get(name)
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise self.error_type("시트 관계 후보 개수는 양의 정수여야 합니다.")
        if candidate["matched_key_count"] > min(candidate["left_distinct_count"], candidate["right_distinct_count"]):
            raise self.error_type("겹치는 키 수가 고유 키 수를 넘을 수 없습니다.")

    def _validate_coverage(self, candidate):
        for name in ("left_coverage", "right_coverage"):
            coverage = candidate.get(name)
            if not isinstance(coverage, (int, float)) or isinstance(coverage, bool):
                raise self.error_type("시트 관계 후보 겹침 비율 형식이 올바르지 않습니다.")
            coverage = float(coverage)
            if not 0.0 <= coverage <= 1.0:
                raise self.error_type("시트 관계 후보 겹침 비율이 올바르지 않습니다.")
            candidate[name] = round(coverage, 4)
        expected_left = round(candidate["matched_key_count"] / candidate["left_distinct_count"], 4)
        expected_right = round(candidate["matched_key_count"] / candidate["right_distinct_count"], 4)
        if candidate["left_coverage"] != expected_left or candidate["right_coverage"] != expected_right:
            raise self.error_type("시트 관계 후보 겹침 비율 검증에 실패했습니다.")
        if candidate["match_basis"] == "value_overlap" and (
            candidate["matched_key_count"] < 3
            or min(candidate["left_coverage"], candidate["right_coverage"]) < 0.8
            or candidate["cardinality"] == "many_to_many"
        ):
            raise self.error_type("이름이 다른 키 후보가 안전한 겹침 기준을 충족하지 않습니다.")

    def _validate_confidence(self, candidate):
        if not isinstance(candidate.get("sample_limited"), bool):
            raise self.error_type("시트 관계 후보 표본 상태가 올바르지 않습니다.")
        if not isinstance(candidate.get("requires_preaggregation"), bool):
            raise self.error_type("시트 관계 후보 집계 필요 형식이 올바르지 않습니다.")
        required = candidate["cardinality"] == "many_to_many"
        if candidate["requires_preaggregation"] is not required:
            raise self.error_type("시트 관계 후보 집계 필요 상태가 올바르지 않습니다.")
        expected = (
            "high"
            if candidate["match_basis"] == "normalized_header"
            and not candidate["ambiguous"]
            and not candidate["sample_limited"]
            and min(candidate["left_coverage"], candidate["right_coverage"]) >= 0.8
            and not required
            else "review_required"
        )
        confidence = str(candidate.get("confidence") or "")
        if confidence != expected:
            raise self.error_type("시트 관계 후보 신뢰 상태가 올바르지 않습니다.")
        candidate["confidence"] = confidence


class WorkflowStateService:
    """Resolve one create/resume intent into a validated workflow state."""

    def __init__(self, adapter, error_type):
        self.adapter = adapter
        self.error_type = error_type

    def build(self, intent):
        source_path = self.adapter._path(self.adapter.session.get("file_path"))
        if intent.operation != "create_business_workflow":
            state = self.adapter.workflow_executor.latest_for_source(source_path)
            if state is None:
                raise self.error_type(
                    "이 Excel 파일에서 재개할 미완료 문서 워크플로를 찾지 못했습니다."
                )
            return state
        self._validate_selection_errors(intent)
        join_plan = self._join_plan(intent, source_path)
        self._validate_scope(intent)
        options = self._output_options(intent, source_path)
        return self.adapter.workflow_executor.prepare(
            source_path,
            output_dir=options["preferences"].get("preferred_output_dir"),
            preferences=options["preferences"],
            slide_count=options["slide_count"],
            explicit_slide_count=bool(intent.params.get("explicit_slide_count")),
            report_format=options["report_format"],
            include_presentation=options["include_presentation"],
            include_report=options["include_report"],
            join_plan=join_plan,
            source_scope=intent.params.get("source_scope"),
        )

    def _validate_selection_errors(self, intent):
        error = intent.params.get("presentation_selection_error") or intent.params.get("report_selection_error")
        if error:
            raise self.error_type(str(error))

    def _join_plan(self, intent, source_path):
        join_plan = intent.params.get("join_plan")
        if intent.params.get("relationship_candidate_requested"):
            error = str(intent.params.get("relationship_candidate_error") or "").strip()
            if error:
                raise WorkflowJoinValidationError(error)
            candidate = self.adapter.workflow_executor.resolve_relationship_candidate(
                source_path=source_path,
                source_fingerprint=file_fingerprint(source_path),
                edit_session_id=self.adapter.session.get("session_id"),
                candidate_index=intent.params.get("relationship_candidate_index"),
            )
            if candidate.get("requires_preaggregation"):
                raise WorkflowJoinValidationError(
                    "선택한 후보는 양쪽 키가 중복된 N:M 관계라 번호만으로 "
                    "조인하지 않습니다. 양쪽 시트·키와 필요한 사전 집계를 직접 말해주세요."
                )
            join_plan = {
                "left_sheet": candidate["left_sheet"],
                "right_sheet": candidate["right_sheet"],
                "left_key": candidate["left_key"],
                "right_key": candidate["right_key"],
                "join_type": intent.params.get("relationship_candidate_join_type"),
            }
        if intent.params.get("join_requested") and not join_plan:
            raise WorkflowJoinValidationError(
                str(intent.params.get("join_error") or "")
                or "조인할 두 시트·양쪽 키·결합 방식을 모두 지정해주세요."
            )
        return join_plan

    @staticmethod
    def _validate_scope(intent):
        if intent.params.get("source_scope_requested") and not intent.params.get("source_scope"):
            raise WorkflowSourceScopeValidationError(
                str(intent.params.get("source_scope_error") or "")
                or "분석할 Excel 선택 범위를 다시 지정해주세요."
            )

    def _output_options(self, intent, source_path):
        preferences = self.adapter.resolved_workflow_preferences(source_path)
        reuse = bool(intent.params.get("reuse_approved_skill"))
        skill = self.adapter.workflow_skill_manager.active_skill() if reuse else None
        if reuse and skill is None:
            raise self.error_type(
                "승인된 지난 업무 스킬이 없습니다. 먼저 성공한 워크플로를 "
                "기억하도록 승인해주세요."
            )
        template = dict((skill or {}).get("template") or {})
        preferred_slides = template.get("slide_count") if reuse else preferences.get("ppt_slide_count", 5)
        if intent.params.get("explicit_include_presentation"):
            include_presentation = bool(intent.params.get("include_presentation"))
        else:
            include_presentation = bool(template.get("include_presentation", True)) if reuse else True
        if intent.params.get("explicit_include_report"):
            include_report = bool(intent.params.get("include_report"))
        else:
            include_report = bool(template.get("include_report", True)) if reuse else True
        if intent.params.get("explicit_slide_count") or reuse:
            preferences = self.adapter._without_applied_preference(preferences, "ppt_slide_count")
        return {
            "preferences": preferences,
            "slide_count": intent.params.get("slide_count") or preferred_slides,
            "report_format": intent.params.get("report_format") or template.get("report_format") or "word",
            "include_presentation": include_presentation,
            "include_report": include_report,
        }


class WorkflowActionExecutionService:
    """Dispatch prepared Stage 10 actions by domain instead of one long branch."""

    def __init__(self, adapter, error_type, operation_groups):
        self.adapter = adapter
        self.error_type = error_type
        self.groups = operation_groups

    def execute(self, prepared):
        operation = prepared.operation
        if operation in self.groups["inspection"]:
            return self._inspection(prepared)
        if operation in self.groups["artifact"]:
            return self._artifact(prepared)
        if operation == "activate_business_workflow_skill":
            active = self.adapter.workflow_skill_manager.activate(prepared.arguments.get("candidate_id"))
            return {"changed": False, "verified": True, "status": "active", "workflow_skill": active, "raw_paths_or_content_stored": False}
        if operation == "deactivate_business_workflow_skill":
            removed = self.adapter.workflow_skill_manager.deactivate()
            return {"changed": False, "verified": True, "status": "deactivated" if removed else "already_inactive", "raw_paths_or_content_stored": False}
        result = self._workflow(prepared)
        result["changed"] = False
        if prepared.arguments.get("workflow_skill_reused"):
            result["workflow_skill_reused"] = True
            result["workflow_skill_candidate_id"] = dict(prepared.arguments.get("workflow_skill") or {}).get("candidate_id")
        return result

    def _inspection(self, prepared):
        source = self.adapter._path(prepared.arguments.get("source_path"))
        expected = dict(prepared.arguments.get("source_fingerprint") or {})
        if source != self.adapter._path(self.adapter.session.get("file_path")) or file_fingerprint(source) != expected:
            raise self.error_type("관계 후보 검사 준비 이후 Excel 원본이 바뀌어 중단했습니다.")
        inspector = getattr(self.adapter.workflow_executor.analyzer, "relationship_candidates", None)
        if not callable(inspector):
            raise self.error_type("Excel 관계 후보 검사기를 사용할 수 없습니다.")
        inspection = self.adapter._validated_relationship_inspection(inspector({"source_path": source}))
        if file_fingerprint(source) != expected:
            raise self.error_type("관계 후보 검사 중 Excel 원본 파일이 바뀌어 결과를 폐기했습니다.")
        self.adapter.workflow_executor.remember_relationship_candidates(
            source_path=source,
            source_fingerprint=expected,
            edit_session_id=prepared.edit_session_id,
            candidates=inspection["candidates"],
        )
        return {**inspection, "changed": False, "verified": True, "source_unchanged": True}

    def _artifact(self, prepared):
        artifact = dict(prepared.arguments.get("artifact") or {})
        path = str(artifact.get("path") or "")
        expected = dict(artifact.get("fingerprint") or {})
        if file_fingerprint(path) != expected:
            raise self.error_type("최근 산출물이 준비 이후 이동·수정·교체되어 열지 않았습니다.")
        document = dict(self.adapter.workflow_artifact_intake.connect_file(path))
        if (
            self.adapter._path(document.get("file_path")) != self.adapter._path(path)
            or str(document.get("app_type") or "").casefold() != str(artifact.get("app_type") or "").casefold()
            or file_fingerprint(path) != expected
        ):
            raise self.error_type("열린 문서가 검증된 최근 산출물과 일치하지 않아 중단했습니다.")
        activation = self._activate_artifact(int(document.get("window_handle") or 0))
        return {
            "changed": False,
            "verified": True,
            "status": "focused",
            "workflow_id": artifact.get("workflow_id"),
            "artifact_kind": artifact.get("artifact_kind"),
            "artifact_label": artifact.get("label"),
            "file_path": path,
            "file_name": Path(path).name,
            "app_type": artifact.get("app_type"),
            "launch_requested": bool(document.get("launch_requested")),
            "activation": activation,
            "edit_session_handoff_requested": prepared.operation == "connect_recent_workflow_artifact",
        }

    def _activate_artifact(self, handle):
        ready = getattr(self.adapter.workflow_artifact_activator, "activate_when_ready", None)
        activation = dict(
            ready(handle, timeout=2.5)
            if callable(ready)
            else self.adapter.workflow_artifact_activator.activate(handle)
        )
        if not activation.get("success") or not activation.get("focused"):
            raise self.error_type(
                "최근 산출물은 열었지만 해당 문서 창을 전면으로 가져온 것을 확인하지 못했습니다."
            )
        return activation

    def _workflow(self, prepared):
        if prepared.operation == "create_business_workflow":
            return dict(self.adapter.workflow_executor.start(dict(prepared.arguments.get("workflow_plan") or {})))
        return dict(self.adapter.workflow_executor.run(str(prepared.arguments.get("workflow_id") or "")))


class WorkflowActionPreparationService:
    """Build approval previews and prepared actions for workflow execution."""

    def __init__(self, adapter, error_type, operation_groups):
        self.adapter = adapter
        self.error_type = error_type
        self.groups = operation_groups

    def prepare(self, request, context, intent):
        if intent.operation in self.groups["skill"]:
            return self.adapter._prepare_workflow_skill_action(request, context, intent)
        if intent.operation in self.groups["artifact"]:
            return self.adapter._prepare_recent_artifact_action(request, context, intent)
        if intent.operation in self.groups["inspection"]:
            return self.adapter._prepare_relationship_inspection_action(request, context, intent)
        state = self.adapter._workflow_state(intent)
        if self.adapter._path(state.get("source_path")) != self.adapter._path(
            self.adapter.session.get("file_path")
        ):
            raise self.error_type("연결된 Excel 파일과 다른 워크플로는 실행하지 않습니다.")
        details = self._preview_details(state, intent)
        return self._prepared_action(request, context, intent, state, details)

    def _preview_details(self, state, intent):
        outputs = dict(state.get("output_paths") or {})
        failed_step = str(state.get("failed_step") or "")
        before = f"상태 {state.get('status')}" + (
            f" · 실패 단계 {failed_step}" if failed_step else ""
        )
        report_format = str(state.get("report_format") or "word")
        include_report = state.get("include_report") is not False
        include_presentation = state.get("include_presentation") is not False
        lines = (
            self.adapter._report_preview_lines(report_format, outputs)
            if include_report
            else ["보고서: 생성하지 않음"]
        )
        if include_presentation:
            lines.append(
                f"PowerPoint({state.get('slide_count', 5)}장): "
                f"{outputs.get('presentation', '')}"
            )
        else:
            lines.append("PowerPoint: 생성하지 않음")
        join_plan = dict(state.get("join_plan") or {})
        source_scope = dict(state.get("source_scope") or {})
        lines.extend(self._join_preview(join_plan))
        if source_scope:
            lines.append(
                "읽기 전용 분석 범위: "
                f"{source_scope.get('sheet_name')}!{source_scope.get('address')} "
                "· 첫 행을 머리글로 사용 · 범위 밖 제외 · Excel 원본 변경 없음"
            )
        preferences, summary = self.adapter._preference_preview(
            state.get("applied_preferences") or {}
        )
        if summary:
            lines.append(f"적용할 학습 기본값: {summary}")
        reused = self._reused_skill(intent, lines)
        preview = {
            "description": intent.description,
            "before": before,
            "after": "\n".join(lines),
            "target": state.get("source_path"),
            "estimated_changes": len(outputs),
            "noop": False,
            "applied_preferences": preferences,
        }
        return {
            "outputs": outputs,
            "report_format": report_format,
            "include_report": include_report,
            "include_presentation": include_presentation,
            "join_plan": join_plan,
            "source_scope": source_scope,
            "preferences": preferences,
            "reused": reused,
            "preview": preview,
        }

    def _join_preview(self, plan):
        if not plan:
            return []
        label = "내부" if plan.get("join_type") == "inner" else "왼쪽"
        key = str(plan.get("left_key") or "")
        if plan.get("left_key") != plan.get("right_key"):
            key += f" ↔ {plan.get('right_key')}"
        aggregation_labels = []
        for side_label, field, sheet in (
            ("왼쪽", "left_aggregation", plan.get("left_sheet")),
            ("오른쪽", "right_aggregation", plan.get("right_sheet")),
        ):
            items = self.adapter.workflow_analyzer._aggregation_items(plan.get(field))
            if items:
                parts = [
                    f"{sheet}/{item.get('column')} "
                    f"{self.adapter.workflow_analyzer._aggregation_function_label(item.get('function'))}"
                    for item in items
                ]
                aggregation_labels.append(f"{side_label} 집계 {' · '.join(parts)}")
        aggregation = f" · {' · '.join(aggregation_labels)}" if aggregation_labels else ""
        return [
            f"읽기 전용 {label} 조인: {plan.get('left_sheet')} ↔ "
            f"{plan.get('right_sheet')} · 키 {key}{aggregation} · Excel 원본 변경 없음"
        ]

    def _reused_skill(self, intent, lines):
        if not intent.params.get("reuse_approved_skill"):
            return None
        active = self.adapter.workflow_skill_manager.active_skill()
        reused = {
            "candidate_id": active.get("candidate_id"),
            "template": dict(active.get("template") or {}),
        }
        lines.append(
            "승인된 재사용 스킬: "
            + self.adapter._workflow_skill_label(reused["template"])
            + " · 현재 Excel 재검증 · 새 산출물 생성"
        )
        return reused

    def _prepared_action(self, request, context, intent, state, details):
        arguments = {
            "workflow_id": state["workflow_id"],
            "source_path": state["source_path"],
            "output_paths": details["outputs"],
            "preview": details["preview"],
            "read_only_source": True,
            "report_format": details["report_format"],
            "include_presentation": details["include_presentation"],
            "include_report": details["include_report"],
            "workflow_skill_reused": bool(details["reused"]),
            "workflow_skill": details["reused"],
            "join_plan": details["join_plan"] or None,
            "source_scope": details["source_scope"] or None,
        }
        if intent.operation == "create_business_workflow":
            arguments["workflow_plan"] = state
        return EditPreparedAction(
            action_id=f"edit-workflow-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type="excel",
            operation=intent.operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": state.get("source_path"),
            },
            arguments=arguments,
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
                {"kind": "workflow_status", "value": state.get("status")},
            ),
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
            verification_plan={"method": "persistent_step_state_and_output_file_fingerprints"},
            rollback_plan={"strategy": "preserve_verified_steps_and_resume_failed_step"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": details["preview"],
                "estimated_changes": len(details["outputs"]),
                "workflow": True,
                "workflow_id": state["workflow_id"],
                "resume": intent.operation == "resume_business_workflow",
                "workflow_skill_reused": bool(details["reused"]),
                "workflow_skill": details["reused"],
                "explicit_join": bool(details["join_plan"]),
                "explicit_source_scope": bool(details["source_scope"]),
                "rewrite_supported": False,
                "applied_user_preferences": details["preferences"],
            },
        )
