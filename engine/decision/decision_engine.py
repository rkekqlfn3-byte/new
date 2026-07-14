"""Explicit safety decisions for prepared native application actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from engine.app_actions.base import PreparedAction


@dataclass(frozen=True)
class DecisionOutcome:
    decision: str
    reason: str | None = None
    message: str | None = None
    recommended_method: str | None = None
    requires_confirmation: bool = False
    preference_key: str | None = None
    rememberable: bool = False
    options: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class DecisionEngine:
    """Use deterministic safety rules until real preference data exists."""

    SUPPORTED = frozenset({
        "write_cell",
        "sum_column_to_cell",
        "apply_conditional_format",
        "format_matching_values",
        "format_range",
        "filter_range",
        "find_replace",
        "sort_range",
        "insert_text",
        "set_text_format",
        "set_paragraph_format",
        "save_as",
    })

    @staticmethod
    def _display(value):
        if value is None:
            return "빈 셀"
        text = str(value).replace("\r", " ").replace("\n", " ")
        return text[:80] + ("…" if len(text) > 80 else "")

    def evaluate(self, prepared: PreparedAction, force_confirmation=False):
        supported_by_app = {
            "excel": {
                "write_cell", "sum_column_to_cell", "apply_conditional_format",
                "format_matching_values", "format_range", "filter_range",
                "find_replace", "sort_range",
            },
            "hwp": {
                "insert_text", "find_replace", "set_text_format",
                "set_paragraph_format", "save_as",
            },
        }
        if prepared.operation not in supported_by_app.get(prepared.app, set()):
            return DecisionOutcome(
                decision="blocked",
                reason="unsupported_operation",
                message="아직 안전 정책이 없는 네이티브 작업입니다.",
            )

        operation_labels = {
            "write_cell": ("입력", "write_cell"),
            "sum_column_to_cell": ("합계 입력", "sum_column_to_cell"),
            "apply_conditional_format": ("조건부 서식 적용", "apply_conditional_format"),
            "format_matching_values": ("현재 셀 표시", "format_matching_values"),
            "format_range": ("범위 서식 적용", "format_range"),
            "filter_range": ("필터 적용", "filter_range"),
            "find_replace": ("찾기·바꾸기", "find_replace"),
            "sort_range": ("표 전체 정렬", "sort_range"),
            "insert_text": ("텍스트 입력", "insert_text"),
            "set_text_format": ("글자 서식 적용", "set_text_format"),
            "set_paragraph_format": ("문단 서식 적용", "set_paragraph_format"),
            "save_as": ("PDF 저장", "save_as"),
        }
        action_label, method = operation_labels[prepared.operation]
        desired = prepared.params.get("value")
        if prepared.operation in {
            "apply_conditional_format", "format_matching_values"
        }:
            desired = (
                f"{prepared.params.get('threshold')} {prepared.params.get('operator')} / "
                f"{prepared.params.get('color')}"
            )
        elif prepared.operation == "format_range":
            desired = prepared.params.get("desired")
        elif prepared.operation == "filter_range":
            desired = (
                "필터 해제" if prepared.params.get("clear") else
                f"{prepared.params.get('column_name')}열 / {prepared.params.get('criteria')}"
            )
        elif prepared.operation == "find_replace":
            desired = (
                f"'{prepared.params.get('find')}' → '{prepared.params.get('replace')}' / "
                f"{prepared.current_state.get('matching_count', 0)}개 "
                f"{'항목' if prepared.app == 'hwp' else '셀'}"
            )
        elif prepared.operation == "sort_range":
            direction = (
                "내림차순" if prepared.params.get("direction") == "descending"
                else "오름차순"
            )
            desired = f"{prepared.params.get('column_name')}열 기준 {direction}"
        elif prepared.operation == "insert_text":
            desired = prepared.params.get("text")
        elif prepared.operation == "set_text_format":
            desired = prepared.params.get("format_labels") or prepared.params.get("desired")
        elif prepared.operation == "set_paragraph_format":
            desired = prepared.params.get("alignment")
        elif prepared.operation == "save_as":
            desired = f"PDF 저장: {prepared.params.get('path')}"
        current = (
            prepared.current_state.get("formula")
            if prepared.current_state.get("formula") is not None
            else prepared.current_state.get("value")
        )
        if force_confirmation:
            reason = "context_changed"
            message = (
                f"확인하는 동안 {'Excel' if prepared.app == 'excel' else '한글'} 상태가 바뀌었습니다. "
                f"현재 {prepared.workbook_name} / {prepared.sheet} / {prepared.target}에 "
                f"'{self._display(desired)}' 작업을 적용할까요?"
            )
        elif prepared.destructive:
            reason = "destructive_action"
            if prepared.operation in {
                "find_replace", "sort_range", "format_range", "insert_text",
                "set_text_format", "set_paragraph_format", "save_as",
            }:
                message = (
                    f"{prepared.workbook_name} / {prepared.sheet} / {prepared.target}에 "
                    f"'{self._display(desired)}' 작업을 적용할까요? "
                    f"예상 변경: {prepared.estimated_changes}개"
                )
            else:
                message = (
                    f"{prepared.workbook_name} / {prepared.sheet} / {prepared.target}에 "
                    f"기존 값 '{self._display(current)}'이(가) 있습니다. "
                    f"'{self._display(desired)}'(으)로 바꿀까요?"
                )
        else:
            return DecisionOutcome(
                decision="execute",
                reason="safe_prepared_app_action",
                recommended_method=method,
            )

        return DecisionOutcome(
            decision="confirmation_required",
            reason=reason,
            message=message,
            recommended_method=method,
            requires_confirmation=True,
            options=[
                {
                    "id": "write" if prepared.operation == "write_cell" else "apply",
                    "label": action_label,
                    "description": (
                        "표시된 Excel 대상에 준비된 작업을 적용합니다."
                        if prepared.app == "excel" else
                        "표시된 한글 문서 대상에 준비된 작업을 적용합니다."
                    ),
                    "recommended": False,
                    "danger": bool(prepared.destructive or force_confirmation),
                    "aliases": [
                        "네", "예", "응", "계속", "적용", "실행",
                        "입력", "바꿔", "덮어써", "합계 입력",
                    ],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": (
                        "Excel을 변경하지 않습니다."
                        if prepared.app == "excel" else
                        "한글 문서와 대상 파일을 변경하지 않습니다."
                    ),
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마"],
                },
            ],
        )
