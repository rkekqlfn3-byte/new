"""Prototype 1.1 Stage 10 resumable cross-application document workflows."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from engine.edit_mode.contracts import EditPreparedAction, EditRequest, RiskLevel
from engine.edit_mode.stage9 import Stage9EditError, Stage9NativeEditAdapter
from engine.learning import BusinessWorkflowSkillManager
from engine.workflows import WorkflowExecutor


WORKFLOW_EXECUTION_OPERATIONS = frozenset(
    {"create_business_workflow", "resume_business_workflow"}
)
WORKFLOW_SKILL_OPERATIONS = frozenset({
    "activate_business_workflow_skill",
    "deactivate_business_workflow_skill",
})
WORKFLOW_OPERATIONS = WORKFLOW_EXECUTION_OPERATIONS | WORKFLOW_SKILL_OPERATIONS


class Stage10EditError(Stage9EditError):
    pass


@dataclass(frozen=True)
class WorkflowIntent:
    operation: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)


class StructuredWorkflowIntentAnalyzer:
    """Recognize only the Stage 10 Excel-to-report-and-slides commands."""

    CREATE_REPORT_TERMS = (
        "보고서", "리포트", "report", "word", "워드", "한글", "hwp", "hwpx"
    )
    CREATE_SLIDE_TERMS = ("ppt", "파워포인트", "프레젠테이션", "발표자료", "슬라이드")
    RESUME_TERMS = (
        "실패한 워크플로 이어서",
        "실패 단계부터",
        "실패한 단계",
        "중단된 워크플로",
        "워크플로 재시도",
        "워크플로 다시 시작",
        "workflow resume",
        "workflow retry",
    )
    REUSE_TERMS = (
        "지난번처럼",
        "지난번 방식대로",
        "전에 하던 방식대로",
        "기억한 방식대로",
        "같은 업무 방식으로",
    )
    REMEMBER_TERMS = (
        "이 워크플로 기억",
        "이 업무 방식 기억",
        "방금 작업 방식 기억",
        "방금 워크플로 기억",
        "다음에도 이대로",
    )
    FORGET_TERMS = (
        "워크플로 기억 취소",
        "업무 방식 기억 취소",
        "지난번 방식 잊어",
        "워크플로 스킬 해제",
    )
    CURRENT_DOCUMENT_TERMS = (
        "이거",
        "이 자료",
        "이 파일",
        "이 데이터",
        "현재 자료",
        "현재 엑셀",
        "현재 파일",
        "여기 데이터",
    )
    CREATE_ACTION_TERMS = (
        "만들",
        "작성",
        "생성",
        "정리",
        "변환",
    )

    @classmethod
    def _creation_params(
        cls,
        command: str,
        *,
        default_report_format: str | None,
    ) -> dict[str, Any]:
        word_explicit = any(term in command for term in ("word", "워드"))
        hwp_explicit = any(term in command for term in ("한글", "hwp", "hwpx"))
        if word_explicit and hwp_explicit:
            report_format = "both"
        elif hwp_explicit:
            report_format = "hwp"
        elif word_explicit:
            report_format = "word"
        else:
            report_format = default_report_format
        slide_count = None
        for pattern in (
            r"(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드).*?(\d{1,2})\s*장",
            r"(\d{1,2})\s*장(?:짜리)?\s*(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드)",
        ):
            match = re.search(pattern, command)
            if match:
                slide_count = int(match.group(1))
                break
        return {
            "slide_count": slide_count,
            "explicit_slide_count": slide_count is not None,
            "report_format": report_format,
            "explicit_report_format": bool(word_explicit or hwp_explicit),
        }

    def analyze(self, text: str, context: Mapping[str, Any]) -> WorkflowIntent | None:
        if str(context.get("app_type") or "").casefold() != "excel":
            return None
        command = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
        if any(term in command for term in self.FORGET_TERMS):
            return WorkflowIntent(
                "deactivate_business_workflow_skill",
                "승인된 복합 업무 재사용 스킬 해제",
            )
        if any(term in command for term in self.REMEMBER_TERMS):
            return WorkflowIntent(
                "activate_business_workflow_skill",
                "검증된 최근 복합 업무 구조를 재사용 스킬로 활성화",
            )
        if any(term in command for term in self.RESUME_TERMS):
            return WorkflowIntent(
                "resume_business_workflow",
                "저장된 성공 단계는 건너뛰고 실패한 문서 워크플로 단계부터 재개",
            )
        if any(term in command for term in self.REUSE_TERMS):
            params = self._creation_params(
                command,
                default_report_format=None,
            )
            params["reuse_approved_skill"] = True
            return WorkflowIntent(
                "create_business_workflow",
                "승인된 지난 복합 업무 구조를 현재 Excel에서 새 산출물로 재사용",
                params,
            )
        has_analysis = any(term in command for term in ("분석", "요약", "analy"))
        has_report = any(term in command for term in self.CREATE_REPORT_TERMS)
        has_slides = any(term in command for term in self.CREATE_SLIDE_TERMS)
        contextual_current_document = bool(
            any(term in command for term in self.CURRENT_DOCUMENT_TERMS)
            and any(term in command for term in self.CREATE_ACTION_TERMS)
        )
        if (has_analysis or contextual_current_document) and has_report and has_slides:
            params = self._creation_params(
                command,
                default_report_format="word",
            )
            params["contextual_current_document"] = contextual_current_document
            report_format = str(params["report_format"])
            report_label = {
                "word": "Word",
                "hwp": "한글",
                "both": "Word·한글",
            }[report_format]
            slide_count = params["slide_count"]
            source_label = (
                "현재 연결 Excel 전체 읽기 전용 분석"
                if contextual_current_document
                else "Excel 읽기 전용 분석"
            )
            description = (
                f"{source_label} → {report_label} 보고서 → "
                f"PowerPoint {slide_count}장 요약 생성"
                if slide_count is not None
                else (
                    f"{source_label} → {report_label} 보고서 → "
                    "PowerPoint 요약 생성"
                )
            )
            return WorkflowIntent(
                "create_business_workflow",
                description,
                params,
            )
        return None


class Stage10NativeEditAdapter(Stage9NativeEditAdapter):
    """Stage 9 plus one persistent Excel-to-report-and-PowerPoint workflow."""

    supported_operations = Stage9NativeEditAdapter.supported_operations | WORKFLOW_OPERATIONS

    def __init__(
        self,
        *args,
        workflow_executor=None,
        workflow_analyzer=None,
        workflow_skill_manager=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.workflow_executor = workflow_executor or WorkflowExecutor()
        self.workflow_analyzer = workflow_analyzer or StructuredWorkflowIntentAnalyzer()
        self.workflow_skill_manager = (
            workflow_skill_manager or BusinessWorkflowSkillManager()
        )

    @staticmethod
    def _path(value) -> str:
        return os.path.normcase(os.path.abspath(str(value or "")))

    @staticmethod
    def _report_preview_lines(
        report_format: str,
        outputs: Mapping[str, Any],
    ) -> list[str]:
        if report_format == "both":
            return [
                f"Word: {outputs.get('report_word', '')}",
                f"한글: {outputs.get('report_hwp', '')}",
            ]
        label = "한글" if report_format == "hwp" else "Word"
        return [f"{label}: {outputs.get('report', '')}"]

    def resolved_workflow_preferences(self, source_path: str) -> dict[str, Any]:
        return {}

    @staticmethod
    def _without_applied_preference(
        preferences: Mapping[str, Any], name: str
    ) -> dict[str, Any]:
        """Remove an active default that an explicit request overrides."""
        result = dict(preferences or {})
        result.pop(name, None)
        metadata = result.get("_learning_metadata")
        if isinstance(metadata, Mapping):
            metadata = dict(metadata)
            metadata.pop(name, None)
            if metadata:
                result["_learning_metadata"] = metadata
            else:
                result.pop("_learning_metadata", None)
        return result

    @staticmethod
    def _preference_preview(preferences: Mapping[str, Any]) -> tuple[dict, str]:
        values = {
            key: value for key, value in dict(preferences or {}).items()
            if key != "_learning_metadata"
        }
        labels = {
            "summary_lines": "요약 줄 수",
            "report_tone": "보고서 문체",
            "title_style": "제목 방식",
            "number_format": "숫자 형식",
            "table_style": "표 방식",
            "ppt_slide_count": "PPT 장수",
            "preferred_output_dir": "저장 위치",
            "confirmation_actions": "확인 정책",
            "workflow_order": "워크플로 순서",
            "emphasis_style": "글자 강조",
            "font_scale": "글자 크기",
            "paragraph_align": "문단 정렬",
        }
        value_labels = {
            "formal": "격식체", "concise": "간결하게", "friendly": "친근하게",
            "bold": "굵게", "regular": "강조 없음",
            "larger": "크게", "smaller": "작게",
            "left": "왼쪽", "center": "가운데", "right": "오른쪽",
            "justify": "양쪽",
        }
        parts = []
        for name, value in values.items():
            label = labels.get(name, name)
            rendered = value_labels.get(value, value) if isinstance(value, str) else value
            if isinstance(rendered, (list, tuple)):
                rendered = ", ".join(str(item) for item in rendered)
            parts.append(f"{label}={rendered}")
        return values, " · ".join(parts)

    @staticmethod
    def _workflow_skill_label(template: Mapping[str, Any]) -> str:
        report_label = {
            "word": "Word",
            "hwp": "한글",
            "both": "Word·한글",
        }.get(str(template.get("report_format") or ""), "보고서")
        return f"Excel 분석 → {report_label} 보고서 → PowerPoint {template.get('slide_count')}장"

    def _prepare_workflow_skill_action(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
        intent: WorkflowIntent,
    ) -> EditPreparedAction:
        if intent.operation == "activate_business_workflow_skill":
            candidate = self.workflow_skill_manager.latest_candidate()
            if candidate is None:
                raise Stage10EditError(
                    "기억할 수 있는 검증 완료 업무가 없습니다. 먼저 문서 워크플로를 "
                    "성공시킨 뒤 다시 요청해주세요."
                )
            template = dict(candidate.get("template") or {})
            active = self.workflow_skill_manager.active_skill()
            before = (
                self._workflow_skill_label(active.get("template") or {})
                if active else "활성 복합 업무 스킬 없음"
            )
            after = self._workflow_skill_label(template)
            operation = intent.operation
            candidate_id = candidate["candidate_id"]
            description = "검증된 복합 업무 구조를 승인형 재사용 스킬로 활성화"
        else:
            active = self.workflow_skill_manager.active_skill()
            if active is None:
                raise Stage10EditError("해제할 승인 복합 업무 스킬이 없습니다.")
            template = dict(active.get("template") or {})
            before = self._workflow_skill_label(template)
            after = "활성 복합 업무 스킬 없음"
            operation = intent.operation
            candidate_id = active["candidate_id"]
            description = "승인된 복합 업무 재사용 스킬 해제"
        preview = {
            "description": description,
            "before": before,
            "after": after,
            "target": "복합 업무 재사용 스킬",
            "estimated_changes": 1,
            "noop": False,
            "raw_paths_or_content_stored": False,
        }
        return EditPreparedAction(
            action_id=f"edit-workflow-skill-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type="excel",
            operation=operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": "복합 업무 재사용 스킬",
            },
            arguments={
                "candidate_id": candidate_id,
                "template": template,
                "preview": preview,
                "read_only_document": True,
            },
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
            ),
            risk_level=RiskLevel.MEDIUM,
            requires_approval=True,
            verification_plan={"method": "local_workflow_skill_store_readback"},
            rollback_plan={"strategy": "keep_previous_active_skill_on_failure"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": 1,
                "workflow_skill": True,
                "candidate_id": candidate_id,
                "rewrite_supported": False,
            },
        )

    def _workflow_state(self, intent: WorkflowIntent) -> dict[str, Any]:
        source_path = self._path(self.session.get("file_path"))
        if intent.operation == "create_business_workflow":
            preferences = self.resolved_workflow_preferences(source_path)
            reuse = bool(intent.params.get("reuse_approved_skill"))
            active_skill = (
                self.workflow_skill_manager.active_skill() if reuse else None
            )
            if reuse and active_skill is None:
                raise Stage10EditError(
                    "승인된 지난 업무 스킬이 없습니다. 먼저 성공한 워크플로를 "
                    "기억하도록 승인해주세요."
                )
            template = dict((active_skill or {}).get("template") or {})
            preferred_slides = (
                template.get("slide_count")
                if reuse
                else preferences.get("ppt_slide_count", 5)
            )
            slide_count = intent.params.get("slide_count") or preferred_slides
            report_format = (
                intent.params.get("report_format")
                or template.get("report_format")
                or "word"
            )
            if intent.params.get("explicit_slide_count"):
                preferences = self._without_applied_preference(
                    preferences, "ppt_slide_count"
                )
            elif reuse:
                # The explicitly approved workflow template is more specific
                # than a broad slide-count preference. Other current approved
                # style preferences are deliberately resolved fresh.
                preferences = self._without_applied_preference(
                    preferences, "ppt_slide_count"
                )
            output_dir = preferences.get("preferred_output_dir")
            return self.workflow_executor.prepare(
                source_path,
                output_dir=output_dir,
                preferences=preferences,
                slide_count=slide_count,
                explicit_slide_count=bool(intent.params.get("explicit_slide_count")),
                report_format=report_format,
            )
        state = self.workflow_executor.latest_for_source(source_path)
        if state is None:
            raise Stage10EditError(
                "이 Excel 파일에서 재개할 미완료 문서 워크플로를 찾지 못했습니다."
            )
        return state

    def prepare(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> EditPreparedAction:
        intent = self.workflow_analyzer.analyze(request.text, context)
        if intent is None:
            return super().prepare(request, context)
        if intent.operation in WORKFLOW_SKILL_OPERATIONS:
            return self._prepare_workflow_skill_action(
                request,
                context,
                intent,
            )
        state = self._workflow_state(intent)
        if self._path(state.get("source_path")) != self._path(self.session.get("file_path")):
            raise Stage10EditError("연결된 Excel 파일과 다른 워크플로는 실행하지 않습니다.")
        outputs = dict(state.get("output_paths") or {})
        failed_step = str(state.get("failed_step") or "")
        before = (
            f"상태 {state.get('status')}"
            + (f" · 실패 단계 {failed_step}" if failed_step else "")
        )
        report_format = str(state.get("report_format") or "word")
        after_lines = self._report_preview_lines(report_format, outputs)
        after_lines.append(
            f"PowerPoint({state.get('slide_count', 5)}장): "
            f"{outputs.get('presentation', '')}"
        )
        after = "\n".join(after_lines)
        applied_preferences, preference_summary = self._preference_preview(
            state.get("applied_preferences") or {}
        )
        if preference_summary:
            after += f"\n적용할 학습 기본값: {preference_summary}"
        reused_skill = None
        if intent.params.get("reuse_approved_skill"):
            active_skill = self.workflow_skill_manager.active_skill()
            reused_skill = {
                "candidate_id": active_skill.get("candidate_id"),
                "template": dict(active_skill.get("template") or {}),
            }
            after += (
                "\n승인된 재사용 스킬: "
                + self._workflow_skill_label(reused_skill["template"])
                + " · 현재 Excel 재검증 · 새 산출물 생성"
            )
        preview = {
            "description": intent.description,
            "before": before,
            "after": after,
            "target": state.get("source_path"),
            "estimated_changes": len(outputs),
            "noop": False,
            "applied_preferences": applied_preferences,
        }
        arguments = {
            "workflow_id": state["workflow_id"],
            "source_path": state["source_path"],
            "output_paths": outputs,
            "preview": preview,
            "read_only_source": True,
            "report_format": report_format,
            "workflow_skill_reused": bool(reused_skill),
            "workflow_skill": reused_skill,
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
            verification_plan={
                "method": "persistent_step_state_and_output_file_fingerprints"
            },
            rollback_plan={
                "strategy": "preserve_verified_steps_and_resume_failed_step"
            },
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": len(outputs),
                "workflow": True,
                "workflow_id": state["workflow_id"],
                "resume": intent.operation == "resume_business_workflow",
                "workflow_skill_reused": bool(reused_skill),
                "workflow_skill": reused_skill,
                "rewrite_supported": False,
                "applied_user_preferences": applied_preferences,
            },
        )

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().execute(prepared_action)
        if prepared_action.operation == "activate_business_workflow_skill":
            active = self.workflow_skill_manager.activate(
                prepared_action.arguments.get("candidate_id")
            )
            return {
                "changed": False,
                "verified": True,
                "status": "active",
                "workflow_skill": active,
                "raw_paths_or_content_stored": False,
            }
        if prepared_action.operation == "deactivate_business_workflow_skill":
            removed = self.workflow_skill_manager.deactivate()
            return {
                "changed": False,
                "verified": True,
                "status": "deactivated" if removed else "already_inactive",
                "raw_paths_or_content_stored": False,
            }
        if prepared_action.operation == "create_business_workflow":
            result = dict(
                self.workflow_executor.start(
                    dict(prepared_action.arguments.get("workflow_plan") or {})
                )
            )
        else:
            result = dict(
                self.workflow_executor.run(
                    str(prepared_action.arguments.get("workflow_id") or "")
                )
            )
        # The connected Excel document itself remains read-only and therefore
        # must not create an undo/continuation record in the edit session.
        result["changed"] = False
        if prepared_action.arguments.get("workflow_skill_reused"):
            result["workflow_skill_reused"] = True
            result["workflow_skill_candidate_id"] = (
                dict(prepared_action.arguments.get("workflow_skill") or {})
                .get("candidate_id")
            )
        return result

    def verify(
        self,
        prepared_action: EditPreparedAction,
        result: Mapping[str, Any],
    ) -> bool:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().verify(prepared_action, result)
        if prepared_action.operation in WORKFLOW_SKILL_OPERATIONS:
            return bool(result.get("verified"))
        report_format = str(
            result.get("report_format")
            or prepared_action.arguments.get("report_format")
            or "word"
        )
        expected_artifacts = 3 if report_format == "both" else 2
        verified = bool(
            result.get("success")
            and result.get("verified")
            and result.get("status") == "completed"
            and len(result.get("created_files") or []) == expected_artifacts
        )
        if verified:
            candidate = self.workflow_skill_manager.record_verified_success(
                result,
                evidence_id=str(result.get("workflow_id") or ""),
            )
            if isinstance(result, dict):
                result["workflow_skill_candidate"] = candidate
        return verified

    def rollback(self, prepared_action: EditPreparedAction) -> bool:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().rollback(prepared_action)
        # Completed prior steps are intentional, verified artifacts.  Keeping
        # them is what makes failure-only retry safe and avoids duplicates.
        return False
