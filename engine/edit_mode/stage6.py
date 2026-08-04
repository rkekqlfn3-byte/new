"""Stage 6 bounded Word and PowerPoint editing on the Stage 5 contract."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from engine.app_actions import PreparedAction
from engine.edit_mode.stage5 import (
    EditIntent,
    Stage5EditError,
    Stage5NativeEditAdapter,
    StructuredEditIntentAnalyzer,
    _formalize_text,
    _quoted_values,
    _shorten_text,
)
from engine.vocabulary.alignment import ALIGNMENT_COMMAND_PATTERN


class StructuredStage6IntentAnalyzer(StructuredEditIntentAnalyzer):
    """Map bounded Word and PowerPoint requests to native operations."""

    @staticmethod
    def _need_text_selection(context: Mapping[str, Any], app_label: str) -> None:
        if int(context.get("selected_text_length") or 0) <= 0:
            raise Stage5EditError(f"{app_label}에서 변경할 텍스트를 먼저 선택해주세요.")

    @staticmethod
    def _inspect_intent(context: Mapping[str, Any]) -> EditIntent:
        app_type = str(context.get("app_type") or "").casefold()
        target = dict(context.get("target") or {})
        if app_type == "word":
            details = [
                f"스타일 {target.get('style_name') or '확인 불가'}",
                f"굵기 {target.get('bold', '확인 불가')}",
                f"글자 크기 {target.get('font_size', '확인 불가')}",
                f"문단 정렬 {target.get('paragraph_alignment', '확인 불가')}",
            ]
            if context.get("selection_kind") == "table_cell":
                details.append(
                    f"표 셀 R{target.get('table_row')}C{target.get('table_column')}"
                )
        else:
            role = (
                f"Placeholder {target['placeholder_type']}"
                if target.get("placeholder_type") is not None
                else target.get("shape_name") or "Shape"
            )
            details = [
                f"슬라이드 {target.get('slide_number')}",
                str(role),
                f"위치 ({target.get('left')}, {target.get('top')})",
                f"크기 {target.get('width')} × {target.get('height')}",
            ]
            if target.get("font_size") is not None:
                details.extend([
                    f"글자 크기 {target.get('font_size')}",
                    f"굵기 {target.get('bold')}",
                    f"정렬 {target.get('paragraph_alignment')}",
                ])
        summary = "현재 대상: " + " · ".join(details)
        return EditIntent(
            operation="inspect_context",
            params={},
            description="현재 스타일·구조 확인",
            after_preview=summary,
            read_only=True,
        )

    def analyze(
        self,
        text: str,
        context: Mapping[str, Any],
        selection_reader=None,
    ) -> EditIntent:
        command = re.sub(r"\s+", " ", str(text or "")).strip()
        if not command:
            raise Stage5EditError("편집 명령이 비어 있습니다.")
        app_type = str(context.get("app_type") or "").casefold()
        if app_type not in {"word", "powerpoint"}:
            return super().analyze(command, context, selection_reader)
        inspect_terms = (
            "스타일", "style", "서식", "표 셀", "현재 셀",
            "placeholder", "플레이스홀더", "위치", "크기",
        )
        inspect_words = ("알려", "보여", "확인", "뭐", "어떤")
        change_words = ("바꿔", "적용", "설정", "정렬", "굵게", "키워", "줄여")
        if (
            any(word in command.casefold() for word in inspect_terms)
            and any(word in command for word in inspect_words)
            and not any(word in command for word in change_words)
        ):
            return self._inspect_intent(context)
        if self._is_read_request(command):
            return self._read_intent(context)
        if app_type == "word":
            return self._analyze_word(command, context, selection_reader)
        return self._analyze_powerpoint(command, context)

    def _analyze_word(self, command, context, selection_reader) -> EditIntent:
        quotes = _quoted_values(command)
        if any(word in command for word in ("저장", "save")):
            return EditIntent("save_document", {}, "Word 문서 저장", after_preview="현재 파일에 저장")

        desired: dict[str, Any] = {}
        labels = []
        if "굵게 해제" in command or "굵지 않게" in command:
            desired["bold"] = False
            labels.append("굵게 해제")
        elif "굵게" in command or "볼드" in command:
            desired["bold"] = True
            labels.append("굵게")
        size = re.search(r"(?:글자|폰트)?\s*크기(?:를|는)?\s*(\d+(?:\.\d+)?)", command)
        if size:
            desired["font_size"] = float(size.group(1))
            labels.append(f"글자 크기 {size.group(1)}pt")
        elif any(
            word in command
            for word in ("조금 크게", "조금 더 크게", "한 단계 크게")
        ):
            desired["font_size_delta"] = 2.0
            labels.append("글자 크기 +2pt")
        elif any(word in command for word in ("조금 작게", "한 단계 작게")):
            desired["font_size_delta"] = -2.0
            labels.append("글자 크기 -2pt")
        if desired:
            self._need_text_selection(context, "Word")
            return EditIntent(
                "set_text_format",
                desired,
                " · ".join(labels),
                after_preview=" · ".join(labels),
            )

        alignment = re.search(ALIGNMENT_COMMAND_PATTERN, command)
        if alignment:
            return EditIntent(
                "set_paragraph_format",
                {"alignment": alignment.group(1)},
                f"문단 {alignment.group(1)} 정렬",
                after_preview=f"문단 {alignment.group(1)} 정렬",
            )

        if quotes and any(word in command for word in ("바꿔", "교체", "입력", "넣어")):
            self._need_text_selection(context, "Word")
            return EditIntent(
                "replace_selection",
                {"text": quotes[-1]},
                "Word 선택 텍스트 교체",
                before_preview=str(context.get("selected_text_preview") or ""),
                after_preview=quotes[-1],
            )

        transform = None
        description = ""
        if any(word in command for word in ("줄여", "축약", "간결하게")):
            transform = _shorten_text
            description = "Word 선택 문장 간단 축약"
        elif any(word in command for word in ("격식체", "보고서체", "공손하게", "문체", "자연스럽게")):
            transform = _formalize_text
            description = "Word 선택 문장 문체 정리"
        if transform:
            self._need_text_selection(context, "Word")
            if not callable(selection_reader):
                raise Stage5EditError("Word 선택 텍스트를 다시 읽지 못했습니다.")
            selected = str(selection_reader() or "")
            changed = transform(selected)
            return EditIntent(
                "replace_selection",
                {"text": changed},
                description,
                before_preview=selected,
                after_preview=changed,
            )

        raise Stage5EditError(
            "지원하는 Word 편집 예: 선택 문장을 “...”로 바꿔줘, 굵게, "
            "글자 크기 12, 가운데 정렬, 저장해줘"
        )

    def _analyze_powerpoint(self, command, context) -> EditIntent:
        target = dict(context.get("target") or {})
        if context.get("selection_kind") not in {"shapes", "text"}:
            raise Stage5EditError("PowerPoint에서 편집할 Shape 또는 텍스트를 먼저 선택해주세요.")
        quotes = _quoted_values(command)
        if any(word in command for word in ("앞 장", "앞 슬라이드", "이전 슬라이드")) and any(
            word in command for word in ("같은 형식", "같은 스타일", "스타일 맞춰", "형식 맞춰")
        ):
            return EditIntent(
                "match_previous_style",
                {},
                "앞 슬라이드의 같은 역할 스타일 적용",
                after_preview="앞 슬라이드 동일 역할 Shape의 텍스트 스타일",
            )

        if quotes and any(word in command for word in ("바꿔", "교체", "입력", "넣어")):
            return EditIntent(
                "replace_shape_text",
                {"text": quotes[-1]},
                "PowerPoint Shape 텍스트 교체",
                before_preview=str(context.get("selected_text_preview") or ""),
                after_preview=quotes[-1],
            )

        desired: dict[str, Any] = {}
        labels = []
        if "굵게 해제" in command or "굵지 않게" in command:
            desired["bold"] = False
            labels.append("굵게 해제")
        elif "굵게" in command or "볼드" in command:
            desired["bold"] = True
            labels.append("굵게")
        size = re.search(r"(?:글자|폰트)?\s*크기(?:를|는)?\s*(\d+(?:\.\d+)?)", command)
        if size:
            desired["font_size"] = float(size.group(1))
            labels.append(f"글자 크기 {size.group(1)}pt")
        elif "크게" in command and (
            any(word in command for word in ("제목", "텍스트", "글자", "폰트"))
            or (
                # 텍스트 선택에서는 대상 단어 없는 "조금 크게"도 Word와 같은
                # ±2pt 축약 표현으로 해석한다. Shape 선택은 기존 크기 분기 유지.
                "조금 크게" in command
                and not quotes
                and context.get("selection_kind") == "text"
            )
        ):
            desired["font_size_delta"] = 2.0
            labels.append("글자 크기 +2pt")
        elif "작게" in command and (
            any(word in command for word in ("제목", "텍스트", "글자", "폰트"))
            or (
                "조금 작게" in command
                and not quotes
                and context.get("selection_kind") == "text"
            )
        ):
            desired["font_size_delta"] = -2.0
            labels.append("글자 크기 -2pt")
        if desired:
            self._need_text_selection(context, "PowerPoint")
            return EditIntent(
                "set_text_format",
                desired,
                " · ".join(labels),
                after_preview=" · ".join(labels),
            )

        alignment = re.search(ALIGNMENT_COMMAND_PATTERN, command)
        if alignment:
            return EditIntent(
                "set_text_alignment",
                {"alignment": alignment.group(1)},
                f"PowerPoint 텍스트 {alignment.group(1)} 정렬",
                after_preview=f"텍스트 {alignment.group(1)} 정렬",
            )

        move = re.search(r"(왼쪽|오른쪽|위|아래)(?:으로)?(?:\s*(\d+(?:\.\d+)?)\s*pt)?", command)
        if move and any(
            word in command
            for word in (
                "이동", "옮겨", "밀어", "왼쪽으로", "오른쪽으로", "위로", "아래로",
            )
        ):
            amount = float(move.group(2) or 10)
            direction = move.group(1)
            delta = {
                "왼쪽": (-amount, 0),
                "오른쪽": (amount, 0),
                "위": (0, -amount),
                "아래": (0, amount),
            }[direction]
            return EditIntent(
                "move_shape",
                {"dx": delta[0], "dy": delta[1]},
                f"Shape {direction} {amount:g}pt 이동",
                before_preview=f"({target.get('left')}, {target.get('top')})",
                after_preview=f"{direction} {amount:g}pt",
            )

        if any(word in command for word in ("도형", "shape", "상자", "크기")) and any(
            word in command for word in ("크게", "키워", "작게", "줄여")
        ):
            scale = 1.1 if any(word in command for word in ("크게", "키워")) else 0.9
            return EditIntent(
                "resize_shape",
                {"scale": scale},
                f"Shape 크기 {scale:.1f}배",
                before_preview=f"{target.get('width')} × {target.get('height')}",
                after_preview=f"현재 크기의 {scale:.1f}배",
            )

        raise Stage5EditError(
            "지원하는 PowerPoint 편집 예: 제목을 “...”로 바꿔줘, 조금 크게, "
            "가운데 정렬, Shape를 오른쪽으로, 도형을 크게, 앞 슬라이드와 같은 스타일"
        )


class Stage6NativeEditAdapter(Stage5NativeEditAdapter):
    """Add exact Word Range and PowerPoint Shape validation to Stage 5."""

    supported_operations = Stage5NativeEditAdapter.supported_operations | frozenset({
        "inspect_context",
        "replace_selection",
        "save_document",
        "replace_shape_text",
        "set_text_alignment",
        "move_shape",
        "resize_shape",
        "match_previous_style",
    })

    def __init__(self, session, context_manager, native_adapter, analyzer=None):
        super().__init__(
            session,
            context_manager,
            native_adapter,
            analyzer=analyzer or StructuredStage6IntentAnalyzer(),
        )

    def _read_word_selection(self, context: Mapping[str, Any]) -> str:
        reader = getattr(self.native_adapter, "read_selection", None)
        if not callable(reader):
            raise Stage5EditError("Word 선택 텍스트 reader가 준비되지 않았습니다.")
        selected = dict(reader(str(self.session.get("file_path") or "")))
        if self._path(selected.get("document_id")) != self._path(
            self.session.get("file_path")
        ):
            raise Stage5EditError("다른 Word 문서의 선택 텍스트 편집을 차단했습니다.")
        text = str(selected.get("text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
        target = dict(context.get("target") or {})
        if (
            digest != str(context.get("selected_text_digest") or "").upper()
            or len(text) != int(context.get("selected_text_length") or 0)
            or int(selected.get("start") or 0) != int(target.get("start") or 0)
            or int(selected.get("end") or 0) != int(target.get("end") or 0)
        ):
            raise Stage5EditError("Word 선택 Range가 문맥 캡처 후 바뀌어 다시 요청해주세요.")
        return text

    def _selection_reader(self, context: Mapping[str, Any]):
        if self.app_type == "word":
            return lambda: self._read_word_selection(context)
        return super()._selection_reader(context)

    def _native_params(
        self,
        intent: EditIntent,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        params = super()._native_params(intent, context)
        if self.app_type in {"word", "powerpoint"}:
            params["document_path"] = str(self.session.get("file_path") or "")
        return params

    @staticmethod
    def _same_digest(left, right) -> bool:
        return str(left or "").upper() == str(right or "").upper()

    def _validate_native_target(
        self,
        native: PreparedAction,
        context: Mapping[str, Any],
    ) -> None:
        super()._validate_native_target(native, context)
        target = dict(context.get("target") or {})
        state = dict(native.current_state or {})
        if self.app_type == "word":
            if native.operation == "save_document":
                return
            if (
                int(native.params.get("start") or 0) != int(target.get("start") or 0)
                or int(native.params.get("end") or 0) != int(target.get("end") or 0)
                or int(state.get("selected_length") or 0)
                != int(context.get("selected_text_length") or 0)
                or not self._same_digest(
                    state.get("selected_digest"),
                    context.get("selected_text_digest"),
                )
            ):
                raise Stage5EditError(
                    "Word 선택 Range가 네이티브 작업 준비 과정에서 달라져 차단했습니다."
                )
            if context.get("selection_kind") == "table_cell":
                table = dict(state.get("table") or {})
                if (
                    int(table.get("row") or 0) != int(target.get("table_row") or 0)
                    or int(table.get("column") or 0)
                    != int(target.get("table_column") or 0)
                ):
                    raise Stage5EditError("Word 표 셀이 달라져 편집을 차단했습니다.")
        elif self.app_type == "powerpoint":
            expected_type = 3 if context.get("selection_kind") == "text" else 2
            if (
                int(state.get("slide_number") or 0)
                != int(target.get("slide_number") or 0)
                or int(state.get("slide_id") or 0) != int(target.get("slide_id") or 0)
                or int(state.get("shape_id") or 0) != int(target.get("shape_id") or 0)
                or int(state.get("selection_type") or 0) != expected_type
                or int(state.get("selected_length") or 0)
                != int(context.get("selected_text_length") or 0)
                or not self._same_digest(
                    state.get("selected_digest"),
                    context.get("selected_text_digest"),
                )
            ):
                raise Stage5EditError(
                    "PowerPoint 슬라이드·Shape·텍스트 선택이 달라져 편집을 차단했습니다."
                )


Stage6EditError = Stage5EditError
