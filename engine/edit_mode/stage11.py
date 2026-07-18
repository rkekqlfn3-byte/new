"""Prototype 1.1 Stage 11 evidence-based user preference learning."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from engine.edit_mode.contracts import EditPreparedAction, EditRequest, RiskLevel
from engine.edit_mode.stage10 import Stage10EditError, Stage10NativeEditAdapter
from engine.learning import UserPreferenceLearningManager


LEARNING_OPERATIONS = frozenset(
    {
        "record_user_preference_evidence",
        "activate_user_preference",
        "deactivate_user_preference",
    }
)

PREFERENCE_LABELS = {
    "summary_lines": "보고서 요약 길이",
    "report_tone": "보고서 문체",
    "title_style": "제목 표현",
    "number_format": "숫자 표시 방식",
    "table_style": "표 서식",
    "ppt_slide_count": "PPT 기본 장수",
    "preferred_output_dir": "선호 파일 위치",
    "confirmation_actions": "확인을 요구하는 작업",
    "workflow_order": "반복 작업 순서",
    "vba_edit_pattern": "VBA 수정 방식",
    "emphasis_style": "텍스트 강조 방식",
    "font_scale": "글자 크기 방향",
    "paragraph_align": "문단 정렬 기본값",
}

VALUE_LABELS = {
    "formal": "격식체",
    "concise": "간결체",
    "friendly": "친근한 문체",
    "default": "기본형",
    "short": "짧은 제목",
    "noun": "명사형 제목",
    "sentence": "서술형 제목",
    "plain": "원본 숫자",
    "thousands": "천 단위 구분",
    "currency_krw": "원화 표시",
    "percent": "백분율 표시",
    "basic": "기본 표",
    "header_bold": "머리글 굵게",
    "banded": "줄무늬 표",
    "backup_module": "모듈 원본 백업",
    "backup_active_sheet": "원본 시트 백업",
    "selection_only": "선택 범위만 처리",
    "fix_last_row": "마지막 행 계산 보정",
    "bold": "굵게 강조",
    "regular": "강조 없음",
    "larger": "크게",
    "smaller": "작게",
    "left": "왼쪽 정렬",
    "center": "가운데 정렬",
    "right": "오른쪽 정렬",
    "justify": "양쪽 정렬",
}

GENERALIZATION_MARKERS = ("앞으로", "다음부터", "항상", "매번")


def direct_style_value(command: str) -> tuple[str, Any] | None:
    """Map one allowlisted style phrase to a (preference, value) pair.

    Shared by explicit statements and the activation of waiting direct-edit
    observations so that the hint phrase a user is shown always parses back
    to the same preference and value.
    """
    text = str(command or "")
    if any(term in text for term in ("간결", "짧고 명확")):
        return "report_tone", "concise"
    if any(term in text for term in ("격식", "정중", "공식")):
        return "report_tone", "formal"
    if any(term in text for term in ("친근", "부드럽")):
        return "report_tone", "friendly"
    if "강조" in text and any(term in text for term in ("없이", "빼", "하지 마")):
        return "emphasis_style", "regular"
    if "굵게" in text:
        return "emphasis_style", "bold"
    if any(term in text for term in ("글자", "글씨", "폰트")):
        if "크게" in text:
            return "font_scale", "larger"
        if "작게" in text:
            return "font_scale", "smaller"
    if "정렬" in text:
        for term, mapped in (
            ("가운데", "center"), ("왼쪽", "left"),
            ("오른쪽", "right"), ("양쪽", "justify"),
        ):
            if term in text:
                return "paragraph_align", mapped
    return None


class Stage11EditError(Stage10EditError):
    pass


@dataclass(frozen=True)
class PreferenceIntent:
    preference: str
    value: Any
    scope_kind: str
    scope_id: str
    description: str
    deactivate: bool = False


def _value_label(value) -> str:
    if isinstance(value, list):
        return " → ".join(VALUE_LABELS.get(str(item), str(item)) for item in value)
    return VALUE_LABELS.get(str(value), str(value))


class StructuredPreferenceIntentAnalyzer:
    """Parse a narrow, explainable set of user-default statements."""

    APP_TERMS = {
        "powerpoint": ("ppt", "파워포인트", "프레젠테이션", "슬라이드"),
        "word": ("word", "워드"),
        "hwp": ("hwp", "한글"),
        "excel": ("excel", "엑셀"),
    }

    @staticmethod
    def _quoted(text: str) -> list[str]:
        return [
            first or second
            for first, second in re.findall(r'"([^"\r\n]+)"|“([^”\r\n]+)”', text)
        ]

    def _scope(self, command: str, context: Mapping[str, Any], preference: str):
        if any(term in command for term in ("이 파일만", "현재 파일만", "이 문서만")):
            return "file", str(context.get("file_path") or "")
        if "전역" in command or "모든 업무" in command:
            return "global", "global"
        for app_id, terms in self.APP_TERMS.items():
            if any(term in command for term in terms):
                return "app", app_id
        if any(term in command for term in ("보고서", "리포트", "실적자료", "매출 분석")):
            return "workflow", "business_report"
        if preference in {
            "summary_lines", "report_tone", "title_style", "number_format", "table_style"
        }:
            return "workflow", "business_report"
        if preference == "vba_edit_pattern":
            return "app", "excel"
        return "global", "global"

    @staticmethod
    def _description(preference: str, value) -> str:
        if value is None:
            return f"{PREFERENCE_LABELS[preference]} 선호"
        return f"{PREFERENCE_LABELS[preference]} 기본값을 {_value_label(value)}(으)로 학습"

    @staticmethod
    def _feedback_scope(
        command: str,
        context: Mapping[str, Any],
        fallback: tuple[str, str],
    ) -> tuple[str, str]:
        """Keep one preview correction local unless generalization is explicit."""
        if any(
            term in command
            for term in (
                "앞으로", "다음부터", "항상", "매번", "전역", "모든 업무",
                "이 파일만", "현재 파일만", "이 문서만",
            )
        ):
            return fallback
        file_path = str(context.get("file_path") or "").strip()
        if file_path:
            return "file", file_path
        app_type = str(context.get("app_type") or "").strip().casefold()
        if app_type in StructuredPreferenceIntentAnalyzer.APP_TERMS:
            return "app", app_type
        return fallback

    def analyze_feedback(
        self,
        text: str,
        context: Mapping[str, Any],
    ) -> PreferenceIntent | None:
        """Extract an allowlisted preference from one preview correction.

        This path is intentionally separate from ``analyze``.  Loose phrases
        such as ``좀 더 간결하게`` are feedback only while a concrete preview
        is pending; treating them as normal edit commands would steal document
        edits from the native adapter.
        """
        original = re.sub(r"\s+", " ", str(text or "")).strip()
        command = original.casefold()
        if not command:
            return None

        intent = self.analyze(original, context)
        if intent is not None:
            if intent.deactivate:
                return None
            scope_kind, scope_id = self._feedback_scope(
                command,
                context,
                (intent.scope_kind, intent.scope_id),
            )
            return PreferenceIntent(
                preference=intent.preference,
                value=intent.value,
                scope_kind=scope_kind,
                scope_id=scope_id,
                description=intent.description,
            )

        correction_markers = (
            "다시", "그거 말고", "이렇게 말고", "좀 더", "조금 더", "너무",
            "바꿔", "고쳐", "해줘", "해주세요", "했으면", "이면 돼",
            "앞으로", "다음부터", "항상", "매번",
        )
        if not any(marker in command for marker in correction_markers):
            return None

        preference = None
        value = None
        if "너무 길" in command:
            preference, value = "report_tone", "concise"
        for terms, mapped in (
            (("간결", "군더더기 없이", "짧고 명확"), "concise"),
            (("격식", "공식적", "정중"), "formal"),
            (("친근", "부드럽", "딱딱하지 않"), "friendly"),
        ):
            if preference is not None:
                break
            if any(term in command for term in terms):
                preference, value = "report_tone", mapped
                break
        if preference is None:
            return None

        fallback = self._scope(command, context, preference)
        scope_kind, scope_id = self._feedback_scope(
            command, context, fallback
        )
        return PreferenceIntent(
            preference=preference,
            value=value,
            scope_kind=scope_kind,
            scope_id=scope_id,
            description=self._description(preference, value),
        )

    def analyze(self, text: str, context: Mapping[str, Any]) -> PreferenceIntent | None:
        original = re.sub(r"\s+", " ", str(text or "")).strip()
        command = original.casefold()
        if not original:
            return None
        # A concrete Stage 10 creation request may contain a slide count, but
        # that count is an instruction for this run rather than learning
        # evidence.  Let the workflow analyzer handle it.
        if (
            any(term in command for term in ("분석", "요약", "analy"))
            and any(term in command for term in ("보고서", "리포트", "report", "word", "워드"))
            and any(term in command for term in ("ppt", "파워포인트", "프레젠테이션", "슬라이드"))
            and any(term in command for term in ("만들", "생성", "작성"))
            and "순서" not in command
        ):
            return None
        deactivate = any(
            term in command
            for term in ("선호 취소", "기본값 취소", "학습 취소", "선호 잊어", "기억하지 마")
        )

        preference = None
        value = None
        ppt_patterns = (
            r"(?:ppt|파워포인트|프레젠테이션|슬라이드).*?(\d{1,2})\s*장",
            r"(\d{1,2})\s*장.*?(?:ppt|파워포인트|프레젠테이션|슬라이드)",
        )
        for pattern in ppt_patterns:
            match = re.search(pattern, command)
            if match:
                preference, value = "ppt_slide_count", int(match.group(1))
                break
        if preference is None:
            match = re.search(r"(?:요약|인사이트).*?(\d{1,2})\s*줄", command)
            if match:
                preference, value = "summary_lines", int(match.group(1))
        if preference is None and any(term in command for term in ("문체", "말투", "톤")):
            for term, mapped in (
                ("격식", "formal"), ("공식", "formal"),
                ("간결", "concise"), ("짧게", "concise"),
                ("친근", "friendly"), ("부드럽", "friendly"),
            ):
                if term in command:
                    preference, value = "report_tone", mapped
                    break
        if preference is None and "제목" in command:
            for term, mapped in (
                ("명사형", "noun"), ("서술형", "sentence"),
                ("짧", "short"), ("기본", "default"),
            ):
                if term in command:
                    preference, value = "title_style", mapped
                    break
        if preference is None and "숫자" in command:
            for term, mapped in (
                ("천 단위", "thousands"), ("콤마", "thousands"),
                ("원화", "currency_krw"), ("원 표시", "currency_krw"),
                ("퍼센트", "percent"), ("백분율", "percent"),
                ("그대로", "plain"),
            ):
                if term in command:
                    preference, value = "number_format", mapped
                    break
        if preference is None and "표" in command and any(
            term in command for term in ("서식", "스타일", "머리글", "줄무늬", "기본")
        ):
            for term, mapped in (
                ("머리글", "header_bold"), ("줄무늬", "banded"), ("기본", "basic")
            ):
                if term in command:
                    preference, value = "table_style", mapped
                    break
        if preference is None and any(
            term in command for term in ("파일 위치", "저장 위치", "출력 폴더", "저장 폴더")
        ):
            quotes = self._quoted(original)
            path_match = re.search(r"[A-Za-z]:\\[^\r\n\"”]+", original)
            path = quotes[-1] if quotes else (path_match.group(0).strip() if path_match else "")
            if path:
                preference, value = "preferred_output_dir", path
        if preference is None and any(term in command for term in ("항상 확인", "매번 확인")):
            if "vba" in command and any(term in command for term in ("실행", "매크로")):
                action = "vba_run"
            elif "vba" in command:
                action = "vba_edit"
            elif any(term in command for term in ("생성", "만들")):
                action = "document_creation"
            else:
                action = "document_edit"
            preference, value = "confirmation_actions", [action]
        if preference is None and all(
            term in command for term in ("분석", "보고서", "ppt")
        ) and any(term in command for term in ("순서", "다음", "먼저")):
            preference, value = "workflow_order", [
                "analyze_excel", "create_word_report", "create_powerpoint_summary"
            ]
        if preference is None and "vba" in command:
            for term, mapped in (
                ("원본 시트", "backup_active_sheet"),
                ("선택 범위", "selection_only"),
                ("마지막 행", "fix_last_row"),
                ("모듈 백업", "backup_module"),
            ):
                if term in command:
                    preference, value = "vba_edit_pattern", mapped
                    break
        # Formatting-style defaults need an explicit generalization marker so
        # a plain edit command like "가운데 정렬로 해줘" stays a document edit.
        if (
            preference is None
            and not deactivate
            and any(marker in command for marker in GENERALIZATION_MARKERS)
        ):
            parsed = direct_style_value(command)
            if parsed is not None and parsed[0] in {
                "emphasis_style", "font_scale", "paragraph_align"
            }:
                preference, value = parsed
        if preference is None and deactivate:
            if any(term in command for term in self.APP_TERMS["powerpoint"]):
                preference = "ppt_slide_count"
            elif any(term in command for term in ("요약 길이", "요약 줄")):
                preference = "summary_lines"
            elif any(term in command for term in ("문체", "말투", "톤")):
                preference = "report_tone"
            elif "제목" in command:
                preference = "title_style"
            elif "숫자" in command:
                preference = "number_format"
            elif "표" in command:
                preference = "table_style"
            elif any(term in command for term in ("파일 위치", "저장 위치", "출력 폴더")):
                preference = "preferred_output_dir"
            elif "확인" in command:
                preference = "confirmation_actions"
            elif "순서" in command:
                preference = "workflow_order"
            elif "vba" in command:
                preference = "vba_edit_pattern"
            elif "강조" in command:
                preference = "emphasis_style"
            elif any(term in command for term in ("글자", "글씨", "폰트")):
                preference = "font_scale"
            elif "정렬" in command:
                preference = "paragraph_align"
        if preference is None:
            return None
        scope_kind, scope_id = self._scope(command, context, preference)
        return PreferenceIntent(
            preference=preference,
            value=value,
            scope_kind=scope_kind,
            scope_id=scope_id,
            description=self._description(preference, value),
            deactivate=deactivate,
        )


class Stage11NativeEditAdapter(Stage10NativeEditAdapter):
    """Stage 10 plus conservative user-default learning and application."""

    supported_operations = Stage10NativeEditAdapter.supported_operations | LEARNING_OPERATIONS

    def __init__(
        self,
        *args,
        user_learning_manager=None,
        preference_analyzer=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.user_learning_manager = (
            user_learning_manager or UserPreferenceLearningManager()
        )
        self.preference_analyzer = (
            preference_analyzer or StructuredPreferenceIntentAnalyzer()
        )

    @staticmethod
    def _scope_label(kind: str, identifier: str) -> str:
        if kind == "global":
            return "전역"
        if kind == "app":
            return f"앱({identifier})"
        if kind == "workflow":
            return f"업무({identifier})"
        return f"파일({Path(identifier).name})"

    def _learning_preview(self, intent: PreferenceIntent, record) -> dict[str, Any]:
        count = int(record.get("proposed_count") or 0)
        evidence = int(record.get("evidence_count") or 0)
        confidence = float(record.get("confidence") or 0.0)
        active_value = record.get("current_active_value")
        proposed_value = record.get("proposed_value")
        observed_conflict = bool(record.get("observed_conflicts_with_active"))
        replacement = bool(
            record.get("replacement_candidate")
            and record.get("needs_confirmation")
        )
        scope = self._scope_label(intent.scope_kind, intent.scope_id)
        if replacement:
            before = (
                f"{scope} 범위의 현재 승인 기본값: {_value_label(active_value)} · "
                f"새 값 동일 {count}회 / 전체 증거 {evidence}회"
            )
            after = (
                f"승인 시 새 기본값: {_value_label(proposed_value)} · "
                f"기존 기본값 교체 · 신뢰도 {confidence:.0%}"
            )
            description = (
                f"{PREFERENCE_LABELS[intent.preference]} 기본값 교체 후보"
            )
            resolution = "replace_on_approval"
        elif active_value is not None:
            before = (
                f"{scope} 범위의 현재 승인 기본값: {_value_label(active_value)} · "
                f"전체 증거 {evidence}회"
            )
            if observed_conflict:
                after = (
                    f"현재 기본값 유지 · 다른 값 증거 "
                    f"{int(record.get('conflicting_evidence_count') or 0)}회 · "
                    "교체 조건 미충족"
                )
                resolution = "keep_active_until_repeated_and_approved"
            else:
                after = "현재 승인 기본값 유지"
                resolution = "reinforce_active"
            description = intent.description
        else:
            before = (
                f"{scope} 범위 · 동일 값 {count}회 / 전체 증거 {evidence}회"
            )
            after = (
                f"활성 기본값: {_value_label(intent.value)} · "
                f"신뢰도 {confidence:.0%}"
            )
            description = intent.description
            resolution = (
                "activate_on_approval"
                if record.get("needs_confirmation")
                else "observe_only"
            )
        return {
            "description": description,
            "before": before,
            "after": after,
            "target": PREFERENCE_LABELS[intent.preference],
            "estimated_changes": 1,
            "noop": False,
            "preference_conflict": observed_conflict or replacement,
            "replacement_candidate": replacement,
            "resolution": resolution,
        }

    def _apply_vba_preference(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> tuple[EditRequest, dict[str, Any] | None]:
        if not self.vba_analyzer.is_vba_request(request.text, context):
            return request, None
        resolved = self.user_learning_manager.resolve(
            "vba_edit_pattern",
            app_id="excel",
            file_path=self.session.get("file_path"),
        )
        if not resolved:
            return request, None
        command = str(request.text or "")
        lower = command.casefold()
        is_change = any(
            term in lower for term in ("고쳐", "수정", "바꿔", "교체", "처리하도록")
        )
        explicit = any(
            term in lower for term in ("마지막 행", "선택한 범위만", "원본 시트")
        ) or len(self.preference_analyzer._quoted(command)) >= 2
        value = str(resolved.get("value") or "")
        suffix = {
            "fix_last_row": " 마지막 행을 잘못 찾는 부분을 고쳐줘",
            "selection_only": " 선택한 범위만 처리하도록 바꿔줘",
            "backup_active_sheet": " 실행 전에 원본 시트를 백업하도록 바꿔줘",
        }.get(value)
        if is_change and not explicit and suffix:
            request = replace(request, text=command + suffix)
        return request, resolved

    def _apply_omitted_formatting_preference(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> tuple[EditRequest, dict[str, Any] | None]:
        """Fill one omitted formatting value from an approved local default.

        Explicit formatting words always win.  Word, PowerPoint and HWP all
        route the resolved value through their existing structural
        preview/approval/read-back contracts.
        """
        app_type = str(context.get("app_type") or "").strip().casefold()
        if app_type not in {"word", "powerpoint", "hwp"}:
            return request, None
        command = re.sub(r"\s+", " ", str(request.text or "")).strip()
        lower = command.casefold()
        generic = []

        emphasis_terms = (
            "강조 방식", "강조 스타일", "기본 강조", "평소 강조", "내 강조"
        )
        explicit_emphasis = any(
            term in lower
            for term in ("굵게", "볼드", "굵지 않게", "강조 없이")
        )
        if any(term in lower for term in emphasis_terms) and not explicit_emphasis:
            generic.append("emphasis_style")

        font_terms = (
            "글자 크기", "글씨 크기", "폰트 크기", "텍스트 크기"
        )
        explicit_font = bool(re.search(r"\d+(?:\.\d+)?\s*(?:pt)?", lower)) or any(
            term in lower
            for term in ("크게", "작게", "키워", "줄여", "늘려")
        )
        if (
            any(term in lower for term in font_terms)
            and any(term in lower for term in ("맞춰", "기본", "평소", "바꿔", "설정"))
            and not explicit_font
        ):
            generic.append("font_scale")

        explicit_alignment = any(
            term in lower for term in ("왼쪽", "가운데", "중앙", "오른쪽", "양쪽")
        )
        excluded_alignment_target = any(
            term in lower for term in ("표 정렬", "도형 정렬", "shape 정렬", "슬라이드 정렬")
        )
        if "정렬" in lower and not explicit_alignment and not excluded_alignment_target:
            generic.append("paragraph_align")

        generic = list(dict.fromkeys(generic))
        if not generic:
            return request, None
        if len(generic) != 1:
            raise Stage11EditError(
                "학습 기본값을 사용할 서식 항목을 한 번에 하나만 말해주세요. "
                "예: 글자 크기 맞춰줘, 정렬해줘"
            )
        preference = generic[0]
        try:
            resolved = self.user_learning_manager.resolve(
                preference,
                app_id=app_type,
                file_path=self.session.get("file_path"),
            )
        except Exception:
            resolved = None
        if not resolved:
            return request, None

        value = str(resolved.get("value") or "")
        suffixes = {
            ("emphasis_style", "bold"): " 굵게 해줘",
            ("emphasis_style", "regular"): " 굵게 해제",
            ("font_scale", "larger"): " 조금 크게",
            ("font_scale", "smaller"): " 조금 작게",
            ("paragraph_align", "left"): " 왼쪽 정렬",
            ("paragraph_align", "center"): " 가운데 정렬",
            ("paragraph_align", "right"): " 오른쪽 정렬",
            ("paragraph_align", "justify"): " 양쪽 정렬",
        }
        suffix = suffixes.get((preference, value))
        if not suffix:
            return request, None
        return replace(request, text=command + suffix), {
            "preference": preference,
            "value": value,
            "scope": resolved.get("resolved_scope"),
            "candidate_id": resolved.get("candidate_id"),
            "reason": "omitted_formatting_value",
        }

    @staticmethod
    def _attach_applied_formatting_preference(
        prepared: EditPreparedAction,
        applied: Mapping[str, Any] | None,
    ) -> EditPreparedAction:
        if not applied:
            return prepared
        preference = str(applied.get("preference") or "")
        expected_operations = {
            "emphasis_style": {"set_text_format"},
            "font_scale": {"set_text_format"},
            "paragraph_align": {"set_paragraph_format", "set_text_alignment"},
        }
        if prepared.operation not in expected_operations.get(preference, set()):
            raise Stage11EditError(
                "학습 서식 기본값이 요청한 편집 작업과 일치하지 않아 적용하지 않았습니다."
            )
        details = dict(applied)
        preview = dict(prepared.metadata.get("preview") or {})
        label = _value_label(details.get("value"))
        description = str(preview.get("description") or "").strip()
        preview["description"] = (
            f"{description} · 학습 기본값 {label}" if description
            else f"학습 기본값 {label}"
        )
        preview["applied_user_preference"] = details
        arguments = dict(prepared.arguments)
        arguments["preview"] = preview
        metadata = dict(prepared.metadata)
        metadata["preview"] = preview
        metadata["applied_user_preference"] = details
        return replace(
            prepared,
            arguments=arguments,
            metadata=metadata,
        )

    def _pending_file_candidate_intent(
        self,
        text: str,
    ) -> PreferenceIntent | None:
        """Route a generalization phrase to the waiting file-scope candidate.

        Direct-edit observations accumulate at file scope, and their
        confirmation hint tells the user to say phrases like ``앞으로도
        간결하게 해줘``.  Without this lookup that phrase would create an
        unrelated global or workflow candidate instead of confirming the one
        that is already waiting for approval.
        """
        command = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
        if not command or not any(
            marker in command for marker in GENERALIZATION_MARKERS
        ):
            return None
        parsed = direct_style_value(command)
        if parsed is None:
            return None
        preference, value = parsed
        file_path = str(self.session.get("file_path") or "").strip()
        if not file_path:
            return None
        normalized_path = os.path.normcase(os.path.abspath(file_path))
        try:
            candidates = self.user_learning_manager.list_candidates()
        except Exception:
            return None
        for candidate in candidates:
            if (
                candidate.get("status") == "candidate"
                and candidate.get("preference") == preference
                and candidate.get("scope_kind") == "file"
                and str(candidate.get("scope_id") or "") == normalized_path
                and candidate.get("proposed_value") == value
            ):
                return PreferenceIntent(
                    preference=preference,
                    value=value,
                    scope_kind="file",
                    scope_id=file_path,
                    description=(
                        StructuredPreferenceIntentAnalyzer._description(
                            preference, value
                        )
                    ),
                )
        return None

    def _attach_verified_correction_feedback(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
        prepared: EditPreparedAction,
    ) -> EditPreparedAction:
        stage7 = dict(prepared.metadata.get("stage7") or {})
        if not stage7.get("follow_up"):
            return prepared
        intent = self.preference_analyzer.analyze_feedback(request.text, context)
        if intent is None:
            return prepared
        metadata = dict(prepared.metadata)
        metadata["preference_feedback_candidate"] = {
            "source": "verified_follow_up",
            "preference": intent.preference,
            "value": intent.value,
            "scope_kind": intent.scope_kind,
            "scope_id": intent.scope_id,
            "evidence_id": f"correction-{request.request_id}"[:160],
            "raw_feedback_stored": False,
        }
        return replace(prepared, metadata=metadata)

    def prepare(self, request: EditRequest, context: Mapping[str, Any]) -> EditPreparedAction:
        workflow_analyzer = getattr(self, "workflow_analyzer", None)
        workflow_intent = (
            workflow_analyzer.analyze(request.text, context)
            if workflow_analyzer is not None
            else None
        )
        if workflow_intent is not None and (
            workflow_intent.operation in {
                "activate_business_workflow_skill",
                "deactivate_business_workflow_skill",
                "resume_business_workflow",
                "open_recent_workflow_artifact",
                "connect_recent_workflow_artifact",
            }
            or workflow_intent.params.get("reuse_approved_skill")
            or workflow_intent.params.get("contextual_current_document")
        ):
            # A remembered-workflow command may also contain ``PPT 7장``.
            # The explicit number belongs to this replay, not to long-term
            # preference evidence. Let Stage 10 own the whole command.
            return super().prepare(request, context)
        intent = self._pending_file_candidate_intent(request.text)
        if intent is None:
            intent = self.preference_analyzer.analyze(request.text, context)
        if intent is None:
            resolved_request, applied_vba = self._apply_vba_preference(request, context)
            resolved_request, applied_formatting = (
                self._apply_omitted_formatting_preference(
                    resolved_request, context
                )
            )
            prepared = super().prepare(resolved_request, context)
            if applied_vba and prepared.operation == "vba_replace_module":
                prepared = replace(
                    prepared,
                    metadata={
                        **prepared.metadata,
                        "applied_user_preference": {
                            "preference": "vba_edit_pattern",
                            "value": applied_vba.get("value"),
                            "scope": applied_vba.get("resolved_scope"),
                            "candidate_id": applied_vba.get("candidate_id"),
                        },
                    },
                )
            prepared = self._attach_applied_formatting_preference(
                prepared, applied_formatting
            )
            return self._attach_verified_correction_feedback(
                request, context, prepared
            )
        if intent.deactivate:
            operation = "deactivate_user_preference"
            requires_approval = True
            preview = {
                "description": f"{PREFERENCE_LABELS[intent.preference]} 활성 선호 해제",
                "before": _value_label(intent.value),
                "after": "활성 기본값 없음",
                "target": self._scope_label(intent.scope_kind, intent.scope_id),
                "estimated_changes": 1,
                "noop": False,
            }
            candidate_id = None
            record = {}
        else:
            record = self.user_learning_manager.record_evidence(
                intent.preference,
                intent.value,
                scope_kind=intent.scope_kind,
                scope_id=intent.scope_id,
                evidence_id=request.request_id,
            )
            candidate_id = record["candidate_id"]
            requires_approval = bool(record.get("needs_confirmation"))
            operation = (
                "activate_user_preference"
                if requires_approval
                else "record_user_preference_evidence"
            )
            preview = self._learning_preview(intent, record)
        action_value = (
            record.get("proposed_value")
            if operation == "activate_user_preference"
            else intent.value
        )
        return EditPreparedAction(
            action_id=f"edit-learning-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type=self.app_type,
            operation=operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": PREFERENCE_LABELS[intent.preference],
            },
            arguments={
                "preference": intent.preference,
                "value": action_value,
                "scope_kind": intent.scope_kind,
                "scope_id": intent.scope_id,
                "candidate_id": candidate_id,
                "evidence_count": int(record.get("evidence_count") or 0),
                "confidence": float(record.get("confidence") or 0.0),
                "conflict_state": record.get("conflict_state", "none"),
                "replacement_candidate": bool(
                    record.get("replacement_candidate")
                    and requires_approval
                ),
                "preview": preview,
                "read_only_document": True,
            },
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
            ),
            risk_level=RiskLevel.MEDIUM if requires_approval else RiskLevel.LOW,
            requires_approval=requires_approval,
            verification_plan={"method": "local_preference_store_readback"},
            rollback_plan={"strategy": "deactivate_preference_if_activation_fails"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": 1,
                "user_preference": True,
                "candidate_id": candidate_id,
                "preference": intent.preference,
                "preference_conflict": bool(preview.get("preference_conflict")),
                "replacement_candidate": bool(
                    preview.get("replacement_candidate")
                ),
                "rewrite_supported": False,
            },
        )

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation not in LEARNING_OPERATIONS:
            return super().execute(prepared_action)
        arguments = prepared_action.arguments
        if prepared_action.operation == "activate_user_preference":
            active = self.user_learning_manager.activate(
                arguments.get("candidate_id"),
                expected_value=arguments.get("value"),
            )
            status = "active"
        elif prepared_action.operation == "deactivate_user_preference":
            removed = self.user_learning_manager.deactivate(
                arguments.get("preference"),
                scope_kind=arguments.get("scope_kind"),
                scope_id=arguments.get("scope_id"),
            )
            active = None
            status = "deactivated" if removed else "already_inactive"
        else:
            candidate = self.user_learning_manager.get_candidate(
                arguments.get("candidate_id")
            )
            active = None
            status = str((candidate or {}).get("status") or "observing")
        candidate = (
            self.user_learning_manager.get_candidate(
                arguments.get("candidate_id")
            )
            if arguments.get("candidate_id")
            else None
        )
        return {
            "changed": False,
            "verified": True,
            "learning_status": status,
            "preference": arguments.get("preference"),
            "value": arguments.get("value"),
            "scope_kind": arguments.get("scope_kind"),
            "scope_id": arguments.get("scope_id"),
            "candidate_id": arguments.get("candidate_id"),
            "evidence_count": arguments.get("evidence_count"),
            "active_preference": active,
            "conflict_state": (
                "replaced_active"
                if active and active.get("replaced_previous")
                else str((candidate or {}).get("conflict_state") or "none")
            ),
            "replacement_candidate": bool(
                arguments.get("replacement_candidate")
            ),
            "replaced_previous": bool(
                active and active.get("replaced_previous")
            ),
            "replaced_previous_value": (
                active.get("replaced_previous_value") if active else None
            ),
        }

    def verify(self, prepared_action, result) -> bool:
        if prepared_action.operation not in LEARNING_OPERATIONS:
            verified = super().verify(prepared_action, result)
            feedback = dict(
                prepared_action.metadata.get("preference_feedback_candidate") or {}
            )
            if verified and feedback:
                try:
                    record = self.user_learning_manager.record_evidence(
                        feedback.get("preference"),
                        feedback.get("value"),
                        scope_kind=feedback.get("scope_kind"),
                        scope_id=feedback.get("scope_id"),
                        evidence_id=feedback.get("evidence_id"),
                    )
                    if isinstance(result, dict):
                        result["preference_feedback"] = {
                            "recorded": not bool(record.get("duplicate_evidence")),
                            "source": "verified_follow_up",
                            "preference": feedback.get("preference"),
                            "value": feedback.get("value"),
                            "scope_kind": feedback.get("scope_kind"),
                            "candidate_id": record.get("candidate_id"),
                            "evidence_count": int(record.get("evidence_count") or 0),
                            "status": record.get("status"),
                            "needs_confirmation": bool(record.get("needs_confirmation")),
                            "raw_feedback_stored": False,
                        }
                except Exception:
                    if isinstance(result, dict):
                        result["preference_feedback"] = {
                            "recorded": False,
                            "source": "verified_follow_up",
                            "reason": "learning_unavailable",
                            "raw_feedback_stored": False,
                        }
            return verified
        return bool(result.get("verified"))

    def rollback(self, prepared_action: EditPreparedAction) -> bool:
        if prepared_action.operation not in LEARNING_OPERATIONS:
            return super().rollback(prepared_action)
        return False

    def resolved_workflow_preferences(self, source_path: str) -> dict[str, Any]:
        result = {}
        metadata = {}
        app_for_preference = {
            "summary_lines": "word",
            "report_tone": "word",
            "title_style": "word",
            "number_format": "word",
            "table_style": "word",
            "ppt_slide_count": "powerpoint",
            "preferred_output_dir": "excel",
            "confirmation_actions": "excel",
            "workflow_order": "excel",
            "emphasis_style": "word",
            "font_scale": "word",
            "paragraph_align": "word",
        }
        for name, app_id in app_for_preference.items():
            resolved = self.user_learning_manager.resolve(
                name,
                app_id=app_id,
                workflow_id="business_report",
                file_path=source_path,
            )
            if not resolved:
                continue
            value = resolved["value"]
            if name == "preferred_output_dir" and not os.path.isdir(str(value)):
                continue
            result[name] = value
            metadata[name] = {
                "scope": resolved.get("resolved_scope"),
                "candidate_id": resolved.get("candidate_id"),
            }
        if metadata:
            result["_learning_metadata"] = metadata
        return result
