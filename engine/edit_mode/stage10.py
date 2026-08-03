"""Prototype 1.1 Stage 10 resumable cross-application document workflows."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from engine.edit_mode.contracts import EditPreparedAction, EditRequest, RiskLevel
from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.stage9 import Stage9EditError, Stage9NativeEditAdapter
from engine.edit_mode.stage10_intent_services import (
    ExplicitJoinIntentService,
    WorkflowIntentRoutingService,
    WorkflowOutputSelectionService,
)
from engine.edit_mode.stage10_action_services import (
    RelationshipInspectionValidator,
    WorkflowActionExecutionService,
    WorkflowActionPreparationService,
    WorkflowStateService,
)
from engine.edit_mode.window_layout import DocumentWindowActivator
from engine.learning import BusinessWorkflowSkillManager
from engine.workflows import (
    WorkflowExecutor,
    WorkflowJoinValidationError,
    WorkflowSourceScopeValidationError,
)
from engine.workflows.business_workflow import (
    RELATIONSHIP_CANDIDATE_FIELDS,
    file_fingerprint,
)


WORKFLOW_EXECUTION_OPERATIONS = frozenset(
    {"create_business_workflow", "resume_business_workflow"}
)
WORKFLOW_SKILL_OPERATIONS = frozenset({
    "activate_business_workflow_skill",
    "deactivate_business_workflow_skill",
})
WORKFLOW_ARTIFACT_OPERATIONS = frozenset({
    "open_recent_workflow_artifact",
    "connect_recent_workflow_artifact",
})
WORKFLOW_INSPECTION_OPERATIONS = frozenset({
    "inspect_excel_relationships",
})
WORKFLOW_OPERATIONS = (
    WORKFLOW_EXECUTION_OPERATIONS
    | WORKFLOW_SKILL_OPERATIONS
    | WORKFLOW_ARTIFACT_OPERATIONS
    | WORKFLOW_INSPECTION_OPERATIONS
)


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
    REPORT_ONLY_PATTERNS = (
        r"(?:word|워드|한글|hwp|hwpx)?\s*(?:보고서|리포트|report)"
        r"\s*만(?=\s|$|[.,!?]|\d)(?!\s*(?:아니|말고))",
        r"(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드)(?:는|은)?\s*"
        r"(?:빼|제외|없이|필요\s*없)",
    )
    PRESENTATION_ONLY_PATTERNS = (
        r"(?:ppt|파워포인트|프레젠테이션|발표자료|슬라이드)"
        r"\s*만(?=\s|$|[.,!?]|\d)(?!\s*(?:아니|말고))",
        r"(?:word|워드|한글|hwp|hwpx|보고서|리포트|report)(?:는|은)?\s*"
        r"(?:빼|제외|없이|필요\s*없)",
    )
    RELATIONSHIP_INSPECTION_TERMS = (
        "조인 키 후보",
        "조인 키 찾아",
        "공통 키 후보",
        "공통 키 찾아",
        "연결 키 후보",
        "조인할 키 찾아",
        "연결할 키 찾아",
        "시트 관계 후보",
        "시트 관계 찾아",
    )
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
    RECENT_ARTIFACT_TERMS = (
        "방금 만든",
        "방금 생성한",
        "방금 작성한",
        "아까 만든",
        "최근 만든",
    )
    OPEN_ARTIFACT_TERMS = (
        "열어",
        "보여",
        "앞으로",
        "포커스",
    )
    JOIN_NAME_TOKEN = (
        r'(?:"[^"\r\n]{1,80}"|\'[^\'\r\n]{1,80}\'|'
        r'[^\s,，"\']{1,80})'
    )

    @staticmethod
    def _join_token(value: str) -> str:
        text = str(value or "").strip()
        if (
            len(text) >= 2
            and text[0] == text[-1]
            and text[0] in {'"', "'"}
        ):
            text = text[1:-1]
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _aggregation_items(value) -> list[dict[str, str]]:
        if isinstance(value, Mapping):
            return [dict(value)]
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
        return []

    @staticmethod
    def _aggregation_function_label(function: str) -> str:
        return {
            "sum": "합계",
            "average": "평균",
            "count": "건수",
            "minimum": "최솟값",
            "maximum": "최댓값",
        }.get(str(function or "").casefold(), str(function or ""))

    @classmethod
    def _join_params(cls, command: str) -> dict[str, Any]:
        return ExplicitJoinIntentService(cls).parse(command)

    @classmethod
    def _creation_params(
        cls,
        command: str,
        *,
        default_report_format: str | None,
    ) -> dict[str, Any]:
        return WorkflowOutputSelectionService(cls).parse(
            command,
            default_report_format=default_report_format,
        )

    @staticmethod
    def _source_scope_params(
        command: str,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        requested = bool(re.search(
            r"(?:선택(?:한)?\s*(?:셀\s*)?(?:범위|영역)\s*만|"
            r"이\s*(?:셀\s*)?범위\s*만)",
            str(command or ""),
            re.IGNORECASE,
        ))
        if not requested:
            return {"source_scope_requested": False, "source_scope": None}
        if str(context.get("selection_kind") or "").casefold() != "range":
            return {
                "source_scope_requested": True,
                "source_scope": None,
                "source_scope_error": (
                    "선택 범위만 분석하려면 Excel에서 머리글과 데이터가 있는 "
                    "연속 셀 범위를 먼저 선택해주세요."
                ),
            }
        sheet_name = str(context.get("active_container") or "").strip()
        address = str(context.get("selection_reference") or "").strip()
        if not sheet_name or not address:
            return {
                "source_scope_requested": True,
                "source_scope": None,
                "source_scope_error": (
                    "현재 선택 범위의 시트명과 셀 주소를 확인하지 못했습니다. "
                    "범위를 다시 선택해주세요."
                ),
            }
        return {
            "source_scope_requested": True,
            "source_scope": {
                "kind": "range",
                "sheet_name": sheet_name,
                "address": address,
            },
        }

    @staticmethod
    def _relationship_candidate_params(command: str) -> dict[str, Any]:
        text = re.sub(r"\s+", " ", str(command or "")).strip().casefold()
        requested = (
            "후보" in text
            and bool(re.search(r"(?:조인|결합|\bjoin\b)", text))
            and bool(re.search(
                r"(?<!\d)\d{1,2}\s*(?:번\s*(?:후보)?|번째\s*후보)",
                text,
            ))
        )
        if not requested:
            return {
                "relationship_candidate_requested": False,
                "relationship_candidate_index": None,
                "relationship_candidate_join_type": None,
            }
        if "집계" in text:
            return {
                "relationship_candidate_requested": True,
                "relationship_candidate_index": None,
                "relationship_candidate_join_type": None,
                "relationship_candidate_error": (
                    "후보 번호 요청에서는 집계 조건을 생략해 실행하지 않습니다. "
                    "사전 집계가 필요하면 두 시트·양쪽 키·집계 열과 방식을 "
                    "직접 말해주세요."
                ),
            }
        matches = []
        for pattern in (
            r"(?<!\d)(\d{1,2})\s*번\s*(?:후보)?",
            r"(?<!\d)(\d{1,2})\s*번째\s*후보",
        ):
            matches.extend(int(value) for value in re.findall(pattern, text))
        matches = list(dict.fromkeys(matches))
        if len(matches) != 1 or not 1 <= matches[0] <= 10:
            return {
                "relationship_candidate_requested": True,
                "relationship_candidate_index": None,
                "relationship_candidate_join_type": None,
                "relationship_candidate_error": (
                    "관계 후보 번호는 현재 표시된 1~10번 중 하나만 말해주세요."
                ),
            }
        inner = bool(re.search(r"(?:내부|\binner\b)", text))
        left = bool(re.search(r"(?:왼쪽|\bleft\b)", text))
        if inner == left:
            return {
                "relationship_candidate_requested": True,
                "relationship_candidate_index": matches[0],
                "relationship_candidate_join_type": None,
                "relationship_candidate_error": (
                    "후보로 조인하려면 내부 또는 왼쪽 방식 중 하나만 말해주세요."
                ),
            }
        return {
            "relationship_candidate_requested": True,
            "relationship_candidate_index": matches[0],
            "relationship_candidate_join_type": "inner" if inner else "left",
        }

    def analyze(self, text: str, context: Mapping[str, Any]) -> WorkflowIntent | None:
        return WorkflowIntentRoutingService(self, WorkflowIntent).analyze(
            text, context
        )


class Stage10NativeEditAdapter(Stage9NativeEditAdapter):
    """Stage 9 plus one persistent Excel-to-report-and-PowerPoint workflow."""

    supported_operations = Stage9NativeEditAdapter.supported_operations | WORKFLOW_OPERATIONS

    def __init__(
        self,
        *args,
        workflow_executor=None,
        workflow_analyzer=None,
        workflow_skill_manager=None,
        workflow_artifact_intake=None,
        workflow_artifact_activator=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.workflow_executor = workflow_executor or WorkflowExecutor()
        self.workflow_analyzer = workflow_analyzer or StructuredWorkflowIntentAnalyzer()
        self.workflow_skill_manager = (
            workflow_skill_manager or BusinessWorkflowSkillManager()
        )
        self.workflow_artifact_intake = (
            workflow_artifact_intake or FileIntakeManager()
        )
        self.workflow_artifact_activator = (
            workflow_artifact_activator or DocumentWindowActivator()
        )

    @staticmethod
    def _path(value) -> str:
        return os.path.normcase(os.path.abspath(str(value or "")))

    @staticmethod
    def _validated_relationship_inspection(value) -> dict[str, Any]:
        return RelationshipInspectionValidator(
            Stage10EditError, RELATIONSHIP_CANDIDATE_FIELDS
        ).validate(value)

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
        if template.get("include_presentation") is False:
            return f"Excel 분석 → {report_label} 보고서만"
        if template.get("include_report") is False:
            return f"Excel 분석 → PowerPoint {template.get('slide_count')}장만"
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
        return WorkflowStateService(self, Stage10EditError).build(intent)

    def _prepare_recent_artifact_action(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
        intent: WorkflowIntent,
    ) -> EditPreparedAction:
        source_path = self._path(self.session.get("file_path"))
        artifact = self.workflow_executor.latest_verified_artifact(
            source_path,
            str(intent.params.get("artifact_kind") or ""),
        )
        preview = {
            "description": intent.description,
            "before": "닫혀 있으면 정확한 파일을 열고, 열려 있으면 같은 창을 사용",
            "after": (
                f"{artifact['label']} 전면 포커스 및 새 편집 대상 연결"
                if intent.operation == "connect_recent_workflow_artifact"
                else f"{artifact['label']} 전면 포커스"
            ),
            "target": Path(str(artifact["path"])).name,
            "estimated_changes": 0,
            "noop": False,
        }
        return EditPreparedAction(
            action_id=f"edit-workflow-artifact-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type="excel",
            operation=intent.operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": artifact["path"],
            },
            arguments={
                "artifact": artifact,
                "preview": preview,
                "read_only_source": True,
            },
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
                {"kind": "artifact_fingerprint", "value": artifact["fingerprint"]},
            ),
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            verification_plan={
                "method": "exact_path_file_fingerprint_and_foreground_window"
            },
            rollback_plan={"strategy": "no_document_content_change"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": 0,
                "workflow_artifact": True,
                "workflow_id": artifact["workflow_id"],
                "artifact_kind": artifact["artifact_kind"],
                "edit_session_handoff": (
                    intent.operation == "connect_recent_workflow_artifact"
                ),
                "rewrite_supported": False,
            },
        )

    def _prepare_relationship_inspection_action(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
        intent: WorkflowIntent,
    ) -> EditPreparedAction:
        source_path = self._path(self.session.get("file_path"))
        source_fingerprint = file_fingerprint(source_path)
        preview = {
            "description": intent.description,
            "before": "현재 표시 시트의 머리글·키 겹침·고유성만 검사",
            "after": (
                "후보 시트·키·관계 형태·표본 제한 여부만 표시하고 "
                "셀 값·파일 경로는 보관하지 않음 · 번호 선택용 후보는 현재 "
                "편집 세션 메모리에서 10분 뒤 만료"
            ),
            "target": str(context.get("document_name") or Path(source_path).name),
            "estimated_changes": 0,
            "noop": False,
        }
        return EditPreparedAction(
            action_id=f"edit-workflow-relationship-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type="excel",
            operation=intent.operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": source_path,
            },
            arguments={
                "source_path": source_path,
                "source_fingerprint": source_fingerprint,
                "preview": preview,
                "read_only_source": True,
            },
            preconditions=(
                {
                    "kind": "document_fingerprint",
                    "value": context["document_fingerprint"],
                },
                {
                    "kind": "context_fingerprint",
                    "value": context["context_fingerprint"],
                },
                {"kind": "source_fingerprint", "value": source_fingerprint},
            ),
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            verification_plan={
                "method": "source_fingerprint_and_schema_only_relationships"
            },
            rollback_plan={"strategy": "no_document_or_file_change"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": 0,
                "workflow_relationship_inspection": True,
                "raw_cell_values_stored": False,
                "document_paths_reported": False,
                "rewrite_supported": False,
            },
        )

    def prepare(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> EditPreparedAction:
        intent = self.workflow_analyzer.analyze(request.text, context)
        if intent is None:
            return super().prepare(request, context)
        groups = {
            "skill": WORKFLOW_SKILL_OPERATIONS,
            "artifact": WORKFLOW_ARTIFACT_OPERATIONS,
            "inspection": WORKFLOW_INSPECTION_OPERATIONS,
        }
        return WorkflowActionPreparationService(
            self, Stage10EditError, groups
        ).prepare(request, context, intent)

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().execute(prepared_action)
        groups = {
            "artifact": WORKFLOW_ARTIFACT_OPERATIONS,
            "inspection": WORKFLOW_INSPECTION_OPERATIONS,
        }
        return WorkflowActionExecutionService(
            self, Stage10EditError, groups
        ).execute(prepared_action)

    def verify(
        self,
        prepared_action: EditPreparedAction,
        result: Mapping[str, Any],
    ) -> bool:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().verify(prepared_action, result)
        if prepared_action.operation in WORKFLOW_INSPECTION_OPERATIONS:
            try:
                inspection = self._validated_relationship_inspection({
                    key: result.get(key)
                    for key in (
                        "status",
                        "candidate_count",
                        "candidates",
                        "automatic_execution_allowed",
                        "raw_cell_values_stored",
                        "document_paths_reported",
                    )
                })
                source_unchanged = file_fingerprint(
                    prepared_action.arguments.get("source_path")
                ) == dict(
                    prepared_action.arguments.get("source_fingerprint") or {}
                )
            except Exception:
                return False
            return bool(
                result.get("verified") is True
                and result.get("changed") is False
                and result.get("source_unchanged") is True
                and source_unchanged
                and inspection["candidate_count"]
                == int(result.get("candidate_count") or 0)
            )
        if prepared_action.operation in WORKFLOW_ARTIFACT_OPERATIONS:
            artifact = dict(prepared_action.arguments.get("artifact") or {})
            return bool(
                result.get("verified")
                and result.get("status") == "focused"
                and result.get("artifact_kind") == artifact.get("artifact_kind")
                and self._path(result.get("file_path"))
                == self._path(artifact.get("path"))
                and file_fingerprint(result.get("file_path"))
                == dict(artifact.get("fingerprint") or {})
                and dict(result.get("activation") or {}).get("focused")
            )
        if prepared_action.operation in WORKFLOW_SKILL_OPERATIONS:
            return bool(result.get("verified"))
        report_format = str(
            result.get("report_format")
            or prepared_action.arguments.get("report_format")
            or "word"
        )
        include_presentation = (
            result.get("include_presentation")
            if type(result.get("include_presentation")) is bool
            else prepared_action.arguments.get("include_presentation")
        )
        if type(include_presentation) is not bool:
            return False
        include_report = (
            result.get("include_report")
            if type(result.get("include_report")) is bool
            else prepared_action.arguments.get("include_report")
        )
        if type(include_report) is not bool:
            return False
        expected_artifacts = (
            (2 if report_format == "both" else 1) if include_report else 0
        ) + int(include_presentation)
        verified = bool(
            result.get("success")
            and result.get("verified")
            and result.get("status") == "completed"
            and len(result.get("created_files") or []) == expected_artifacts
            and result.get("step_contracts_verified") is True
            and result.get("registered_step_recipe_verified") is True
            and result.get("step_registry_schema_version") == 1
            and int(result.get("step_contract_count") or 0)
            == expected_artifacts + 1
        )
        if verified:
            if result.get("join_plan") or result.get("source_scope"):
                if isinstance(result, dict):
                    result["workflow_skill_candidate"] = None
                    result["workflow_skill_candidate_suppressed"] = (
                        "explicit_source_parameters_not_persisted"
                    )
            else:
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
