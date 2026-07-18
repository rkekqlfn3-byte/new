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
from engine.edit_mode.window_layout import DocumentWindowActivator
from engine.learning import BusinessWorkflowSkillManager
from engine.workflows import (
    WorkflowExecutor,
    WorkflowJoinValidationError,
    WorkflowSourceScopeValidationError,
)
from engine.workflows.business_workflow import file_fingerprint


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
WORKFLOW_OPERATIONS = (
    WORKFLOW_EXECUTION_OPERATIONS
    | WORKFLOW_SKILL_OPERATIONS
    | WORKFLOW_ARTIFACT_OPERATIONS
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
        """Extract only a complete explicit-key inner/left sheet join request."""
        raw = re.sub(r"\s+", " ", str(command or "")).strip()
        lowered = raw.casefold()
        requested = "시트" in lowered and bool(
            re.search(r"(?:조인|\bjoin\b|결합)", lowered)
        )
        if not requested:
            return {"join_requested": False, "join_plan": None}

        token = cls.JOIN_NAME_TOKEN
        mapped_pair = re.search(
            rf"(?P<left>{token})\s*시트의\s*"
            rf"(?P<left_key>{token})\s*(?:열\s*)?(?:와|과|하고)\s*"
            rf"(?P<right>{token})\s*시트의\s*"
            rf"(?P<right_key>{token})\s*(?:열\s*)?(?:을|를)?\s*"
            rf"(?:키\s*)?(?:로|으로|기준(?:으로)?)",
            raw,
            re.IGNORECASE,
        )
        pair = mapped_pair or re.search(
            rf"(?P<left>{token})\s*시트(?:와|과|하고)\s*"
            rf"(?P<right>{token})\s*시트(?:를|을)?",
            raw,
            re.IGNORECASE,
        )
        if pair is None:
            return {
                "join_requested": True,
                "join_plan": None,
                "join_error": (
                    "조인할 두 시트를 '고객 시트와 주문 시트'처럼 지정하거나, "
                    "키 이름이 다르면 '고객 시트의 고객ID와 주문 시트의 "
                    "구매자ID로'처럼 양쪽 키를 모두 지정해주세요. 공백이 있는 "
                    "시트명과 키는 따옴표로 묶어주세요."
                ),
            }
        suffix = raw[pair.end():]
        join_type_match = re.search(
            r"(?P<type>내부|이너|inner|왼쪽|좌측|left)\s*"
            r"(?:조인|join|결합)",
            suffix,
            re.IGNORECASE,
        )
        if join_type_match is None:
            return {
                "join_requested": True,
                "join_plan": None,
                "join_error": (
                    "조인 방식을 '내부 조인' 또는 '왼쪽 조인'으로 "
                    "명시해주세요."
                ),
            }

        if mapped_pair is not None:
            left_key = cls._join_token(mapped_pair.group("left_key"))
            right_key = cls._join_token(mapped_pair.group("right_key"))
        else:
            key_patterns = (
                rf"(?P<key>{token})\s*(?:열\s*)?(?:을|를)?\s*"
                r"기준(?:으로)?\s*$",
                rf"(?P<key>{token})\s*(?:열\s*)?(?:을|를)?\s*"
                r"(?:키\s*)?(?:로|으로)\s*$",
            )
            key_match = None
            before_type = suffix[:join_type_match.start()].strip()
            after_type = suffix[join_type_match.end():].strip()
            for fragment in (before_type, after_type):
                for pattern in key_patterns:
                    key_match = re.search(pattern, fragment, re.IGNORECASE)
                    if key_match is not None:
                        break
                if key_match is not None:
                    break
            if key_match is None:
                return {
                    "join_requested": True,
                    "join_plan": None,
                    "join_error": (
                        "두 시트에 공통으로 있는 키 열을 '고객ID 기준으로'처럼 "
                        "정확히 지정해주세요."
                    ),
                }
            left_key = right_key = cls._join_token(key_match.group("key"))

        join_type_text = join_type_match.group("type").casefold()
        join_type = (
            "inner"
            if join_type_text in {"내부", "이너", "inner"}
            else "left"
        )
        left_aggregations = []
        right_aggregations = []
        if "집계" in raw:
            aggregation_matches = list(
                re.finditer(
                    rf"(?P<sheet>{token})\s*시트의\s*"
                    rf"(?P<column>{token})\s*(?:열\s*)?(?:을|를|은|는)\s*"
                    r"(?P<function>합계|합산|평균|건수|개수|"
                    r"최솟값|최소값|최소|최댓값|최대값|최대)(?:로)?\s*집계",
                    raw,
                    re.IGNORECASE,
                )
            )
            if not aggregation_matches:
                return {
                    "join_requested": True,
                    "join_plan": None,
                    "join_error": (
                        "집계 조인은 '주문 시트의 매출을 합계 집계해서'처럼 "
                        "시트·열·집계 함수를 정확히 지정해주세요. 여러 열은 "
                        "각 집계마다 해당 시트명을 반복해주세요."
                    ),
                }
            left_sheet = cls._join_token(pair.group("left"))
            right_sheet = cls._join_token(pair.group("right"))
            function_names = {
                "합계": "sum",
                "합산": "sum",
                "평균": "average",
                "건수": "count",
                "개수": "count",
                "최솟값": "minimum",
                "최소값": "minimum",
                "최소": "minimum",
                "최댓값": "maximum",
                "최대값": "maximum",
                "최대": "maximum",
            }
            for aggregation_match in aggregation_matches:
                aggregation_sheet = cls._join_token(
                    aggregation_match.group("sheet")
                )
                if aggregation_sheet.casefold() == left_sheet.casefold():
                    target = left_aggregations
                elif aggregation_sheet.casefold() == right_sheet.casefold():
                    target = right_aggregations
                else:
                    return {
                        "join_requested": True,
                        "join_plan": None,
                        "join_error": (
                            "집계 시트는 조인에 지정한 왼쪽 또는 오른쪽 시트와 "
                            "정확히 같아야 합니다. 각 집계의 시트명을 다시 "
                            "확인해주세요."
                        ),
                    }
                target.append({
                    "column": cls._join_token(
                        aggregation_match.group("column")
                    ),
                    "function": function_names[
                        aggregation_match.group("function").casefold()
                    ],
                })
            if len(left_aggregations) > 5 or len(right_aggregations) > 5:
                return {
                    "join_requested": True,
                    "join_plan": None,
                    "join_error": (
                        "한 요청에서 왼쪽과 오른쪽 집계는 각각 5개까지 "
                        "지정할 수 있습니다."
                    ),
                }
        plan = {
            "left_sheet": cls._join_token(pair.group("left")),
            "right_sheet": cls._join_token(pair.group("right")),
            "left_key": left_key,
            "right_key": right_key,
            "join_type": join_type,
        }
        if left_aggregations:
            plan["left_aggregation"] = (
                left_aggregations[0]
                if len(left_aggregations) == 1
                else left_aggregations
            )
        if right_aggregations:
            plan["right_aggregation"] = (
                right_aggregations[0]
                if len(right_aggregations) == 1
                else right_aggregations
            )
        return {
            "join_requested": True,
            "join_plan": plan,
        }

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

    def analyze(self, text: str, context: Mapping[str, Any]) -> WorkflowIntent | None:
        if str(context.get("app_type") or "").casefold() != "excel":
            return None
        raw_command = re.sub(r"\s+", " ", str(text or "")).strip()
        command = raw_command.casefold()
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
        recent_artifact = any(
            term in command for term in self.RECENT_ARTIFACT_TERMS
        )
        connect_for_edit = "편집" in command and "연결" in command
        open_artifact = any(
            term in command for term in self.OPEN_ARTIFACT_TERMS
        )
        if recent_artifact and (open_artifact or connect_for_edit):
            artifact_kind = None
            label = None
            if any(term in command for term in ("ppt", "파워포인트", "발표자료", "슬라이드")):
                artifact_kind = "presentation"
                label = "최근 검증 PowerPoint 발표자료"
            elif any(term in command for term in ("보고서", "리포트")):
                if any(term in command for term in ("한글", "hwp", "hwpx")):
                    artifact_kind = "hwp_report"
                    label = "최근 검증 한글 보고서"
                elif any(term in command for term in ("word", "워드", "docx")):
                    artifact_kind = "word_report"
                    label = "최근 검증 Word 보고서"
                else:
                    artifact_kind = "report"
                    label = "최근 검증 보고서"
            if artifact_kind:
                operation = (
                    "connect_recent_workflow_artifact"
                    if connect_for_edit
                    else "open_recent_workflow_artifact"
                )
                action_label = (
                    "열기·전면 포커스 후 편집 대상으로 전환"
                    if connect_for_edit
                    else "열기 및 전면 포커스"
                )
                return WorkflowIntent(
                    operation,
                    f"{label} {action_label}",
                    {"artifact_kind": artifact_kind},
                )
        if any(term in command for term in self.REUSE_TERMS):
            params = self._creation_params(
                command,
                default_report_format=None,
            )
            params.update(self._join_params(raw_command))
            params.update(self._source_scope_params(raw_command, context))
            params["reuse_approved_skill"] = True
            return WorkflowIntent(
                "create_business_workflow",
                "승인된 지난 복합 업무 구조를 현재 Excel에서 새 산출물로 재사용",
                params,
            )
        join_params = self._join_params(raw_command)
        scope_params = self._source_scope_params(raw_command, context)
        has_analysis = (
            any(term in command for term in ("분석", "요약", "analy"))
            or bool(join_params.get("join_requested"))
            or bool(scope_params.get("source_scope_requested"))
        )
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
            params.update(join_params)
            params.update(scope_params)
            params["contextual_current_document"] = contextual_current_document
            report_format = str(params["report_format"])
            report_label = {
                "word": "Word",
                "hwp": "한글",
                "both": "Word·한글",
            }[report_format]
            slide_count = params["slide_count"]
            source_label = (
                "현재 선택 Excel 범위만 읽기 전용 분석"
                if params.get("source_scope_requested")
                else (
                    "현재 연결 Excel 전체 읽기 전용 분석"
                    if contextual_current_document
                    else "Excel 읽기 전용 분석"
                )
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
            join_plan = params.get("join_plan")
            if join_plan:
                join_label = (
                    "내부" if join_plan["join_type"] == "inner" else "왼쪽"
                )
                key_description = f"{join_plan['left_key']} 기준"
                if join_plan["left_key"] != join_plan["right_key"]:
                    key_description = (
                        f"{join_plan['left_key']} ↔ "
                        f"{join_plan['right_key']} 키 매핑으로"
                    )
                aggregation_parts = []
                for field, sheet in (
                    ("left_aggregation", join_plan["left_sheet"]),
                    ("right_aggregation", join_plan["right_sheet"]),
                ):
                    aggregation_parts.extend(
                        f"{sheet}/{item['column']} "
                        f"{self._aggregation_function_label(item['function'])}"
                        for item in self._aggregation_items(
                            join_plan.get(field)
                        )
                    )
                aggregation_description = ""
                if aggregation_parts:
                    aggregation_description = (
                        f"{' · '.join(aggregation_parts)} 집계 후 "
                    )
                description = (
                    f"{join_plan['left_sheet']}·{join_plan['right_sheet']} 시트를 "
                    f"{key_description} {aggregation_description}"
                    f"{join_label} 조인 후 " + description
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
            if (
                intent.params.get("join_requested")
                and not intent.params.get("join_plan")
            ):
                raise WorkflowJoinValidationError(
                    str(intent.params.get("join_error") or "")
                    or "조인할 두 시트·양쪽 키·결합 방식을 모두 지정해주세요."
                )
            if (
                intent.params.get("source_scope_requested")
                and not intent.params.get("source_scope")
            ):
                raise WorkflowSourceScopeValidationError(
                    str(intent.params.get("source_scope_error") or "")
                    or "분석할 Excel 선택 범위를 다시 지정해주세요."
                )
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
                join_plan=intent.params.get("join_plan"),
                source_scope=intent.params.get("source_scope"),
            )
        state = self.workflow_executor.latest_for_source(source_path)
        if state is None:
            raise Stage10EditError(
                "이 Excel 파일에서 재개할 미완료 문서 워크플로를 찾지 못했습니다."
            )
        return state

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
        if intent.operation in WORKFLOW_ARTIFACT_OPERATIONS:
            return self._prepare_recent_artifact_action(
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
        join_plan = dict(state.get("join_plan") or {})
        if join_plan:
            join_label = (
                "내부" if join_plan.get("join_type") == "inner" else "왼쪽"
            )
            key_label = str(join_plan.get("left_key") or "")
            if join_plan.get("left_key") != join_plan.get("right_key"):
                key_label += f" ↔ {join_plan.get('right_key')}"
            aggregation_labels = []
            for side_label, field, sheet in (
                ("왼쪽", "left_aggregation", join_plan.get("left_sheet")),
                ("오른쪽", "right_aggregation", join_plan.get("right_sheet")),
            ):
                aggregations = (
                    StructuredWorkflowIntentAnalyzer._aggregation_items(
                        join_plan.get(field)
                    )
                )
                if aggregations:
                    aggregation_parts = [
                        "{}/{} {}".format(
                            sheet,
                            item.get("column"),
                            StructuredWorkflowIntentAnalyzer
                            ._aggregation_function_label(item.get("function")),
                        )
                        for item in aggregations
                    ]
                    aggregation_labels.append(
                        f"{side_label} 집계 {' · '.join(aggregation_parts)}"
                    )
            aggregation_label = (
                f" · {' · '.join(aggregation_labels)}"
                if aggregation_labels
                else ""
            )
            after += (
                f"\n읽기 전용 {join_label} 조인: "
                f"{join_plan.get('left_sheet')} ↔ {join_plan.get('right_sheet')} "
                f"· 키 {key_label}{aggregation_label} "
                "· Excel 원본 변경 없음"
            )
        source_scope = dict(state.get("source_scope") or {})
        if source_scope:
            after += (
                f"\n읽기 전용 분석 범위: "
                f"{source_scope.get('sheet_name')}!{source_scope.get('address')} "
                "· 첫 행을 머리글로 사용 · 범위 밖 제외 · Excel 원본 변경 없음"
            )
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
            "join_plan": join_plan or None,
            "source_scope": source_scope or None,
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
                "explicit_join": bool(join_plan),
                "explicit_source_scope": bool(source_scope),
                "rewrite_supported": False,
                "applied_user_preferences": applied_preferences,
            },
        )

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation not in WORKFLOW_OPERATIONS:
            return super().execute(prepared_action)
        if prepared_action.operation in WORKFLOW_ARTIFACT_OPERATIONS:
            artifact = dict(prepared_action.arguments.get("artifact") or {})
            path = str(artifact.get("path") or "")
            expected_fingerprint = dict(artifact.get("fingerprint") or {})
            if file_fingerprint(path) != expected_fingerprint:
                raise Stage10EditError(
                    "최근 산출물이 준비 이후 이동·수정·교체되어 열지 않았습니다."
                )
            document = dict(self.workflow_artifact_intake.connect_file(path))
            if (
                self._path(document.get("file_path")) != self._path(path)
                or str(document.get("app_type") or "").casefold()
                != str(artifact.get("app_type") or "").casefold()
                or file_fingerprint(path) != expected_fingerprint
            ):
                raise Stage10EditError(
                    "열린 문서가 검증된 최근 산출물과 일치하지 않아 중단했습니다."
                )
            handle = int(document.get("window_handle") or 0)
            activate_when_ready = getattr(
                self.workflow_artifact_activator,
                "activate_when_ready",
                None,
            )
            activation = dict(
                activate_when_ready(handle, timeout=2.5)
                if callable(activate_when_ready)
                else self.workflow_artifact_activator.activate(handle)
            )
            if not activation.get("success") or not activation.get("focused"):
                raise Stage10EditError(
                    "최근 산출물은 열었지만 해당 문서 창을 전면으로 가져온 것을 "
                    "확인하지 못했습니다."
                )
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
                "edit_session_handoff_requested": (
                    prepared_action.operation
                    == "connect_recent_workflow_artifact"
                ),
            }
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
        expected_artifacts = 3 if report_format == "both" else 2
        verified = bool(
            result.get("success")
            and result.get("verified")
            and result.get("status") == "completed"
            and len(result.get("created_files") or []) == expected_artifacts
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
