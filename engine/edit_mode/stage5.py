"""Stage 5 structured intent and native Excel/HWP edit bridge."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from engine.app_actions import PreparedAction
from engine.edit_mode.contracts import (
    EditContractError,
    EditPreparedAction,
    EditRequest,
    RiskLevel,
)
from engine.vocabulary.alignment import ALIGNMENT_COMMAND_PATTERN
from engine.vocabulary.line_spacing import (
    LINE_SPACING_COMMAND_PATTERN,
    line_spacing_label,
    normalize_line_spacing,
)
from engine.vocabulary.list_format import (
    LIST_FORMAT_COMMAND_PATTERN,
    list_format_label,
    normalize_list_format,
)
from engine.vocabulary.table_size import (
    COLUMN_WIDTH_COMMAND_PATTERN,
    column_width_label,
    normalize_column_width_mm,
)

_CELL_OR_RANGE = re.compile(
    r"^([A-Z]{1,3})([1-9]\d*)(?::([A-Z]{1,3})([1-9]\d*))?$",
    re.IGNORECASE,
)
_CELL_REFERENCE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{1,3}[1-9]\d*)(?![A-Za-z0-9_])")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_COLORS = ("검정", "빨강", "초록", "파랑", "노랑", "주황", "회색")


class Stage5EditError(EditContractError):
    status = "blocked"


@dataclass(frozen=True)
class EditIntent:
    operation: str
    params: dict[str, Any]
    description: str
    before_preview: str = ""
    after_preview: str = ""
    read_only: bool = False


def _quoted_values(text: str) -> list[str]:
    values = []
    patterns = (
        r'"([^"\r\n]*)"',
        r"'([^'\r\n]*)'",
        r"“([^”\r\n]*)”",
        r"‘([^’\r\n]*)’",
    )
    for match in re.finditer("|".join(f"(?:{item})" for item in patterns), text):
        value = next((group for group in match.groups() if group is not None), "")
        values.append(value)
    return values


def _without_quoted_values(text: str) -> str:
    return re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'|“[^”\r\n]*”|‘[^’\r\n]*’', " ", text)


def _column_number(value: str) -> int:
    result = 0
    for character in str(value).upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _column_letters(value: int) -> str:
    result = ""
    number = int(value)
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _range_bounds(address: str) -> tuple[int, int, int, int]:
    match = _CELL_OR_RANGE.fullmatch(str(address or "").replace("$", "").upper())
    if not match:
        raise Stage5EditError("현재 Excel 선택 영역의 주소를 안전하게 해석하지 못했습니다.")
    first_column = _column_number(match.group(1))
    first_row = int(match.group(2))
    last_column = _column_number(match.group(3) or match.group(1))
    last_row = int(match.group(4) or match.group(2))
    if last_column < first_column or last_row < first_row:
        raise Stage5EditError("역방향 Excel 선택 영역은 5단계 편집에서 지원하지 않습니다.")
    return first_column, first_row, last_column, last_row


def _shorten_text(value: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or "")).strip()
    if len(text) < 12:
        raise Stage5EditError("선택한 문장이 너무 짧아 축약할 내용이 없습니다.")
    target = max(8, int(len(text) * 0.7))
    sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s+", text) if item.strip()]
    if len(sentences) > 1:
        chosen = []
        length = 0
        for sentence in sentences:
            if chosen and length + 1 + len(sentence) > target:
                break
            chosen.append(sentence)
            length += len(sentence) + (1 if length else 0)
        shortened = " ".join(chosen)
        if shortened and len(shortened) < len(text):
            return shortened
    cut = text.rfind(" ", 0, target + 1)
    if cut < max(4, target // 2):
        cut = target
    return text[:cut].rstrip(" ,;:") + "…"


def _formalize_text(value: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or "")).strip()
    replacements = (
        (r"했어요(?=\s|[.!?]|$)", "했습니다"),
        (r"해요(?=\s|[.!?]|$)", "합니다"),
        (r"이에요(?=\s|[.!?]|$)", "입니다"),
        (r"예요(?=\s|[.!?]|$)", "입니다"),
        (r"있어요(?=\s|[.!?]|$)", "있습니다"),
        (r"없어요(?=\s|[.!?]|$)", "없습니다"),
        (r"돼요(?=\s|[.!?]|$)", "됩니다"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    if text == str(value or "").strip():
        raise Stage5EditError(
            "선택 문장에서 안전하게 바꿀 수 있는 구어체 종결을 찾지 못했습니다. "
            "원하는 문장을 따옴표로 직접 지정해주세요."
        )
    return text


_TABLE_SIZE_RE = re.compile(r"(\d+)\s*(?:행|줄)\D{0,4}?(\d+)\s*(?:열|칸)")


_TABLE_CELL_RE = re.compile(r"(\d+)\s*(?:행|줄)\D{0,4}?(\d+)\s*(?:열|칸)")


def _hwp_table_cell_intent(command: str, quotes):
    if "표" not in command or not quotes:
        return None
    if not any(word in command for word in ("넣어", "입력", "바꿔", "채워", "써")):
        return None
    address = _TABLE_CELL_RE.search(command)
    if not address:
        return None
    row, column = int(address.group(1)), int(address.group(2))
    label = f"표 {row}행 {column}열 입력"
    return EditIntent(
        "set_table_cell",
        {"row": row, "column": column, "text": quotes[-1]},
        label,
        after_preview=label,
    )


_TABLE_STRUCTURE_ACTIONS = (
    # Checked in order; the first match wins, so "삭제" must not be reached by
    # a request that says "추가".
    ("merge_table_cells", ("병합", "합쳐", "합치")),
    ("delete_table_row", ("행 삭제", "행 지워", "줄 삭제", "행 없애")),
    ("delete_table_column", ("열 삭제", "열 지워", "칸 삭제", "열 없애")),
    ("insert_table_row", ("행 추가", "행 넣", "줄 추가", "행 삽입")),
    ("insert_table_column", ("열 추가", "열 넣", "칸 추가", "열 삽입")),
)


def _hwp_table_width_intent(command: str):
    if "표" not in command:
        return None
    if not re.search(COLUMN_WIDTH_COMMAND_PATTERN, command):
        return None
    # Strip the address first so "2열 너비 40mm" does not read 2 as the width.
    address = _TABLE_CELL_RE.search(command)
    row, column = (
        (int(address.group(1)), int(address.group(2))) if address else (1, 1)
    )
    remainder = _TABLE_CELL_RE.sub("", command)
    single = re.search(r"(\d+)\s*열", remainder)
    if single and not address:
        column = int(single.group(1))
        remainder = remainder.replace(single.group(0), "", 1)
    value = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:mm|밀리|미리)?", remainder)
    if not value:
        raise Stage5EditError("열 너비는 '2열 너비 40mm로'처럼 값을 함께 알려주세요.")
    millimetres = normalize_column_width_mm(value.group(1))
    label = f"표 {column}열 " + column_width_label(millimetres)
    return EditIntent(
        "set_table_column_width",
        {"row": row, "column": column, "width_mm": millimetres},
        label,
        after_preview=label,
    )


def _hwp_table_structure_intent(command: str):
    if "표" not in command:
        return None
    for operation, words in _TABLE_STRUCTURE_ACTIONS:
        if not any(word in command for word in words):
            continue
        address = _TABLE_CELL_RE.search(command)
        row, column = (
            (int(address.group(1)), int(address.group(2))) if address else (1, 1)
        )
        label = f"표 {row}행 {column}열 기준 " + {
            "merge_table_cells": "칸 병합",
            "delete_table_row": "행 삭제",
            "delete_table_column": "열 삭제",
            "insert_table_row": "행 추가",
            "insert_table_column": "열 추가",
        }[operation]
        return EditIntent(
            operation, {"row": row, "column": column}, label, after_preview=label
        )
    return None


def _hwp_table_intent(command: str):
    if "표" not in command or not any(
        word in command for word in ("넣어", "삽입", "만들", "추가", "생성")
    ):
        return None
    size = _TABLE_SIZE_RE.search(command)
    if not size:
        raise Stage5EditError("표는 '3행 4열 표 넣어줘'처럼 행과 열을 함께 알려주세요.")
    rows, columns = int(size.group(1)), int(size.group(2))
    label = f"{rows}행 {columns}열 표 삽입"
    return EditIntent(
        "insert_table",
        {"rows": rows, "columns": columns},
        label,
        after_preview=label,
    )


def _hwp_selection_intent(command: str, quotes):
    """한글 intents that need nothing from context beyond the selection."""
    width = _hwp_table_width_intent(command)
    if width is not None:
        return width
    structure = _hwp_table_structure_intent(command)
    if structure is not None:
        return structure
    cell = _hwp_table_cell_intent(command, quotes)
    if cell is not None:
        return cell
    table = _hwp_table_intent(command)
    if table is not None:
        return table
    # Paragraph settings are checked before the generic delete, because
    # `글머리표 없애줘` and `줄간격 없애` name a setting to clear, not text to
    # remove. Ordered the other way, `없애` swallowed them and the preview
    # offered to delete the user's selection instead.
    paragraph = _paragraph_format_intent(command)
    if paragraph is not None:
        return paragraph
    if not quotes and any(
        word in command for word in ("삭제", "지워", "지워줘", "없애", "빼줘")
    ):
        return EditIntent(
            "delete_text",
            {},
            "선택 영역 삭제",
            after_preview="선택 영역 삭제",
        )
    return None


def _paragraph_format_intent(command: str):
    """Page break, list format, line spacing and alignment.

    Ordered most specific first: a request naming a list or a spacing must not
    be read as a bare alignment or a delete.
    """
    if any(word in command for word in ("쪽 나눔", "쪽나눔", "페이지 나눔", "새 쪽", "새 페이지")):
        return EditIntent(
            "insert_page_break",
            {},
            "쪽 나눔",
            after_preview="새 쪽 시작",
        )
    listing = re.search(LIST_FORMAT_COMMAND_PATTERN, command)
    if listing:
        wanted = "none" if any(w in command for w in ("없애", "해제", "지워")) else listing.group(1)
        try:
            canonical = normalize_list_format(wanted)
        except ValueError as error:
            raise Stage5EditError(str(error)) from error
        label = list_format_label(canonical)
        return EditIntent(
            "set_list_format",
            {"list_format": canonical},
            label,
            after_preview=f"문단 {label}",
        )
    if re.search(LINE_SPACING_COMMAND_PATTERN, command):
        try:
            percent = normalize_line_spacing(
                re.sub(LINE_SPACING_COMMAND_PATTERN, "", command)
            )
        except ValueError as error:
            raise Stage5EditError(str(error)) from error
        label = line_spacing_label(percent)
        return EditIntent(
            "set_line_spacing",
            {"line_spacing": percent},
            label,
            after_preview=f"문단 {label}",
        )
    alignment = re.search(ALIGNMENT_COMMAND_PATTERN, command)
    if alignment:
        return EditIntent(
            "set_paragraph_format",
            {"alignment": alignment.group(1)},
            f"{alignment.group(1)} 정렬",
            after_preview=f"문단 {alignment.group(1)} 정렬",
        )
    return None


class StructuredEditIntentAnalyzer:
    """Map bounded Korean edit utterances to allowlisted native operations."""

    @staticmethod
    def _selection_address(context: Mapping[str, Any]) -> str:
        if str(context.get("selection_kind")) != "range":
            raise Stage5EditError("Excel에서 편집할 셀 또는 범위를 먼저 선택해주세요.")
        address = str(context.get("selection_reference") or "").replace("$", "").upper()
        _range_bounds(address)
        return address

    @staticmethod
    def _read_intent(context: Mapping[str, Any]) -> EditIntent:
        preview = str(context.get("selected_text_preview") or "")
        return EditIntent(
            operation="read_selection",
            params={},
            description="선택 영역 읽기",
            before_preview=preview,
            after_preview=preview,
            read_only=True,
        )

    @staticmethod
    def _is_read_request(text: str) -> bool:
        read_words = ("읽어", "보여", "알려", "무슨 값", "내용이 뭐", "선택 내용")
        write_words = ("입력", "바꿔", "교체", "서식", "정렬", "필터", "굵게", "줄여")
        return any(word in text for word in read_words) and not any(
            word in text for word in write_words
        )

    def analyze(self, text: str, context: Mapping[str, Any], selection_reader=None) -> EditIntent:
        command = re.sub(r"\s+", " ", str(text or "")).strip()
        if not command:
            raise Stage5EditError("편집 명령이 비어 있습니다.")
        if self._is_read_request(command):
            return self._read_intent(context)
        app_type = str(context.get("app_type") or "").casefold()
        if app_type == "excel":
            return self._analyze_excel(command, context)
        if app_type == "hwp":
            return self._analyze_hwp(command, context, selection_reader)
        raise Stage5EditError("5단계 실제 편집은 Excel과 한글에서만 지원합니다.")

    def _analyze_excel(self, command: str, context: Mapping[str, Any]) -> EditIntent:
        address = self._selection_address(context)
        first_column, first_row, last_column, last_row = _range_bounds(address)
        quotes = _quoted_values(command)

        if any(
            word in command
            for word in ("행 추가", "행 삽입", "줄 추가", "열 추가", "열 삽입")
        ):
            axis = (
                "column"
                if any(word in command for word in ("열 추가", "열 삽입"))
                else "row"
            )
            label = "열" if axis == "column" else "행"
            count_match = re.search(rf"(\d+)\s*(?:개\s*)?(?:{label})", command)
            count = int(count_match.group(1)) if count_match else 1
            if not 1 <= count <= 10:
                raise Stage5EditError("행·열 추가는 한 번에 1개부터 10개까지 지원합니다.")
            return EditIntent(
                "insert_columns" if axis == "column" else "insert_rows",
                {"selection_range": address, "count": count},
                f"선택 위치에 {count}개 {label} 추가",
                after_preview=f"빈 {label} {count}개 추가",
            )

        if "필터" in command:
            if "해제" in command or "지워" in command or "취소" in command:
                return EditIntent(
                    "filter_range", {"clear": True}, "필터 해제"
                )
            if len(quotes) >= 2:
                column_name, value = quotes[0], quotes[1]
            else:
                match = re.search(r"([^ ]+?)\s*열.*?([^ ]+?)(?:로|으로)\s*필터", command)
                if not match:
                    raise Stage5EditError(
                        '필터는 예: “상태” 열을 “완료”로 필터해줘처럼 지정해주세요.'
                    )
                column_name, value = match.group(1), match.group(2)
            return EditIntent(
                "filter_range",
                {
                    "table_range": address,
                    "column_name": column_name,
                    "operator": "eq",
                    "value": value,
                },
                f"{column_name} 열에서 {value} 필터",
                after_preview=f"{column_name} = {value}",
            )

        if "정렬" in command and any(
            word in command for word in ("오름", "내림", "낮은", "높은", "기준")
        ):
            direction = (
                "descending"
                if any(word in command for word in ("내림", "높은"))
                else "ascending"
            )
            if quotes:
                column_name = quotes[0]
            else:
                match = re.search(r"([^ ]+?)\s*열(?:을|를|\s).*?(?:기준|정렬)", command)
                if match:
                    column_name = match.group(1)
                elif first_column == last_column:
                    column_name = _column_letters(first_column)
                else:
                    raise Stage5EditError("정렬할 열 이름을 함께 지정해주세요.")
            return EditIntent(
                "sort_range",
                {
                    "table_range": address,
                    "column_name": column_name,
                    "direction": direction,
                },
                f"{column_name} 열 기준 {'내림차순' if direction == 'descending' else '오름차순'} 정렬",
            )

        if len(quotes) >= 2 and any(word in command for word in ("바꿔", "교체", "치환")):
            return EditIntent(
                "find_replace",
                {
                    "scope": "range",
                    "range": address,
                    "find": quotes[0],
                    "replace": quotes[1],
                },
                "선택 범위 찾기·바꾸기",
                before_preview=quotes[0],
                after_preview=quotes[1],
            )

        if any(word in command for word in ("합계", "더해", "총합")):
            if first_column != last_column:
                raise Stage5EditError("합계는 한 열로 선택된 범위에서만 지원합니다.")
            target = f"{_column_letters(first_column)}{last_row + 1}"
            explicit = _CELL_REFERENCE.findall(_without_quoted_values(command).upper())
            if explicit:
                target = explicit[-1].upper()
            return EditIntent(
                "sum_column_to_cell",
                {"source_range": address, "target_cell": target, "result_mode": "formula"},
                f"{address} 합계를 {target}에 입력",
                after_preview=f"=SUM({address})",
            )

        if any(word in command for word in ("평균", "AVERAGE")):
            if first_column != last_column:
                raise Stage5EditError("평균은 한 열로 선택된 범위에서만 지원합니다.")
            if (last_row - first_row + 1) > 25:
                raise Stage5EditError("5단계 평균은 한 번에 25개 셀까지 지원합니다.")
            target = f"{_column_letters(first_column)}{last_row + 1}"
            explicit = _CELL_REFERENCE.findall(_without_quoted_values(command).upper())
            if explicit:
                target = explicit[-1].upper()
            formula = f"=AVERAGE({address})"
            return EditIntent(
                "write_cell",
                {"cell": target, "value": formula, "value_type": "formula"},
                f"{address} 평균을 {target}에 입력",
                after_preview=formula,
            )

        desired: dict[str, Any] = {}
        labels = []
        if "굵게" in command or "볼드" in command:
            desired["bold"] = not any(word in command for word in ("해제", "취소", "아니게"))
            labels.append("굵게" if desired["bold"] else "굵게 해제")
        size_match = re.search(r"(?:글자|폰트)?\s*크기(?:를|는)?\s*(\d+(?:\.\d+)?)", command)
        if size_match:
            desired["font_size"] = float(size_match.group(1))
            labels.append(f"글자 크기 {size_match.group(1)}")
        alignment_match = re.search(ALIGNMENT_COMMAND_PATTERN, command)
        if alignment_match:
            desired["alignment"] = alignment_match.group(1)
            labels.append(f"{alignment_match.group(1)} 정렬")
        for color in _COLORS:
            if color not in command:
                continue
            if any(word in command for word in ("배경", "채우기", "셀 색")):
                desired["fill_color"] = color
                labels.append(f"배경 {color}")
            elif any(word in command for word in ("글자색", "폰트색", "글자 색")):
                desired["font_color"] = color
                labels.append(f"글자색 {color}")
        if desired:
            return EditIntent(
                "format_range",
                {"range": address, **desired},
                " · ".join(labels),
                after_preview=" · ".join(labels),
            )

        if (
            not quotes
            and not any(word in command for word in ("입력", "넣어", "써", "적어"))
            and not re.search(r"=[A-Za-z]", command)
        ):
            # 입력 요청이 아닌 미지원·모호 문장은 입력 예시가 아니라
            # 지원 편집 목록으로 안내한다.
            raise Stage5EditError(
                "지원하는 Excel 편집 예: 42 입력해줘, “완료” 입력해줘, 굵게, "
                "글자 크기 14, 가운데 정렬, “대기”를 “완료”로 바꿔줘, "
                "“상태” 열을 “완료”로 필터해줘, 내림차순 정렬, 2개 행 추가"
            )
        explicit_cells = _CELL_REFERENCE.findall(_without_quoted_values(command).upper())
        target = explicit_cells[0].upper() if explicit_cells else address
        target_bounds = _range_bounds(target)
        if target_bounds[0] != target_bounds[2] or target_bounds[1] != target_bounds[3]:
            raise Stage5EditError("값·수식 입력은 셀 하나를 선택해주세요.")
        if target != address and not (
            first_column == last_column and first_row == last_row and target == address
        ):
            raise Stage5EditError("입력할 셀을 Excel에서 먼저 선택해주세요.")

        value = None
        value_type = None
        formula = next((item for item in quotes if item.strip().startswith("=")), None)
        if formula is None:
            formula_match = re.search(r"(=[A-Za-z][^\r\n]*?)(?:\s+(?:입력|넣어|써).*)?$", command)
            formula = formula_match.group(1).strip() if formula_match else None
        if formula:
            value = formula
            value_type = "formula"
        elif quotes:
            value = quotes[0]
            value_type = "text"
        else:
            number_match = re.search(
                r"(-?\d+(?:\.\d+)?)\s*(?:을|를)?\s*(?:입력|넣어|써)", command
            )
            if number_match:
                raw = number_match.group(1)
                value = float(raw) if "." in raw else int(raw)
                value_type = "number"
        if value is None:
            raise Stage5EditError(
                '값 입력은 예: 42 입력해줘, “완료” 입력해줘, “=SUM(A1:A3)” 입력해줘처럼 요청해주세요.'
            )
        return EditIntent(
            "write_cell",
            {"cell": target, "value": value, "value_type": value_type},
            f"{target}에 {'수식' if value_type == 'formula' else '값'} 입력",
            after_preview=str(value),
        )

    def _analyze_hwp(
        self,
        command: str,
        context: Mapping[str, Any],
        selection_reader,
    ) -> EditIntent:
        quotes = _quoted_values(command)
        if len(quotes) >= 2 and any(word in command for word in ("바꿔", "교체", "치환")):
            scope = "document" if "문서 전체" in command else "selection"
            return EditIntent(
                "find_replace",
                {
                    "scope": scope,
                    "find": quotes[0],
                    "replace": quotes[1],
                },
                f"{'문서 전체' if scope == 'document' else '선택 영역'} 찾기·바꾸기",
                before_preview=quotes[0],
                after_preview=quotes[1],
            )

        desired: dict[str, Any] = {}
        labels = []
        if "굵게" in command or "볼드" in command:
            desired["bold"] = not any(word in command for word in ("해제", "취소", "아니게"))
            labels.append("굵게" if desired["bold"] else "굵게 해제")
        size_match = re.search(r"(?:글자|폰트)?\s*크기(?:를|는)?\s*(\d+(?:\.\d+)?)", command)
        if size_match:
            desired["font_size"] = float(size_match.group(1))
            labels.append(f"글자 크기 {size_match.group(1)}")
        elif any(
            term in command for term in ("글자", "글씨", "폰트", "크기")
        ) or (
            # 따옴표 없는 "조금 크게/작게"는 Word와 같은 ±2pt 축약 표현이다.
            not quotes and ("조금 크게" in command or "조금 작게" in command)
        ):
            if any(term in command for term in ("조금 크게", "키워", "늘려")) or (
                "크게" in command
                and any(term in command for term in ("글자", "글씨", "폰트", "크기"))
            ):
                desired["font_size_delta"] = 2.0
                labels.append("글자 크기 +2")
            elif any(term in command for term in ("조금 작게", "작게", "줄여")):
                desired["font_size_delta"] = -2.0
                labels.append("글자 크기 -2")
        if desired:
            return EditIntent(
                "set_text_format",
                desired,
                " · ".join(labels),
                after_preview=" · ".join(labels),
            )

        selection_intent = _hwp_selection_intent(command, quotes)
        if selection_intent is not None:
            return selection_intent

        if quotes and any(
            word in command for word in ("바꿔", "교체", "입력", "넣어", "표 셀")
        ):
            return EditIntent(
                "insert_text",
                {"text": quotes[-1]},
                "선택 텍스트 교체" if context.get("selection_kind") == "text" else "현재 위치 입력",
                before_preview=str(context.get("selected_text_preview") or ""),
                after_preview=quotes[-1],
            )

        if any(word in command for word in ("줄여", "축약", "간결하게")):
            if not callable(selection_reader):
                raise Stage5EditError("한글 선택 텍스트를 다시 읽지 못했습니다.")
            selected_text = str(selection_reader() or "")
            shortened = _shorten_text(selected_text)
            return EditIntent(
                "insert_text",
                {"text": shortened},
                "선택 문단 간단 축약",
                before_preview=selected_text,
                after_preview=shortened,
            )

        if any(word in command for word in ("격식체", "보고서체", "공손하게", "문체")):
            if not callable(selection_reader):
                raise Stage5EditError("한글 선택 텍스트를 다시 읽지 못했습니다.")
            selected_text = str(selection_reader() or "")
            formalized = _formalize_text(selected_text)
            return EditIntent(
                "insert_text",
                {"text": formalized},
                "선택 문장 격식체 변환",
                before_preview=selected_text,
                after_preview=formalized,
            )

        raise Stage5EditError(
            "지원하는 한글 편집 예: 선택 문장을 “...”로 바꿔줘, 조금 줄여줘, "
            "굵게, 글자 크기 12, 조금 크게, 가운데 정렬, “A”를 “B”로 바꿔줘"
        )


class Stage5NativeEditAdapter:
    """Adapt trusted native actions to the common edit-mode action contract."""

    supported_operations = frozenset({
        "read_selection",
        "write_cell",
        "sum_column_to_cell",
        "format_range",
        "filter_range",
        "find_replace",
        "sort_range",
        "insert_rows",
        "insert_columns",
        "insert_text",
        "set_text_format",
        "set_paragraph_format",
        "set_line_spacing",
        "delete_text",
        "insert_table",
        "set_table_cell",
        "set_list_format",
        "insert_page_break",
        "insert_table_row",
        "insert_table_column",
        "delete_table_row",
        "delete_table_column",
        "merge_table_cells",
        "set_table_column_width",
    })

    def __init__(self, session, context_manager, native_adapter, analyzer=None):
        self.session = dict(session)
        self.app_type = str(self.session.get("app_type") or "").casefold()
        self.context_manager = context_manager
        self.native_adapter = native_adapter
        self.analyzer = analyzer or StructuredEditIntentAnalyzer()
        self._last_native_result = None

    def get_context(self) -> Mapping[str, Any]:
        return self.context_manager.capture(self.session)

    @staticmethod
    def fingerprint(context: Mapping[str, Any]) -> str:
        return str(context.get("context_fingerprint") or "").upper()

    @staticmethod
    def _path(value) -> str:
        return os.path.normcase(os.path.abspath(str(value or "")))

    def _read_hwp_selection(self, context: Mapping[str, Any]) -> str:
        reader = getattr(self.native_adapter, "read_selection", None)
        if not callable(reader):
            raise Stage5EditError("한글 선택 텍스트 reader가 준비되지 않았습니다.")
        selected = dict(reader())
        if self._path(selected.get("document_id")) != self._path(self.session.get("file_path")):
            raise Stage5EditError("다른 한글 문서의 선택 텍스트를 읽어 편집을 차단했습니다.")
        text = str(selected.get("text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest().upper()
        if (
            digest != str(context.get("selected_text_digest") or "").upper()
            or len(text) != int(context.get("selected_text_length") or 0)
        ):
            raise Stage5EditError("한글 선택 영역이 문맥 캡처 후 바뀌어 다시 요청해주세요.")
        if not selected.get("has_selection") or not text:
            raise Stage5EditError("변경할 한글 문장을 먼저 선택해주세요.")
        return text

    def _selection_reader(self, context: Mapping[str, Any]):
        if self.app_type == "hwp":
            return lambda: self._read_hwp_selection(context)
        return None

    def _native_params(
        self,
        intent: EditIntent,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        return dict(intent.params)

    def _validate_native_target(self, native: PreparedAction, context: Mapping[str, Any]) -> None:
        if native.app != self.app_type:
            raise Stage5EditError("다른 앱용 작업이 준비되어 실행을 차단했습니다.")
        expected_document_id = self.session.get("file_path")
        if self.app_type == "excel" and self.session.get("identity_kind") == "runtime":
            expected_document_id = f"unsaved:{self.session.get('document_name') or ''}"
        if self._path(native.document_id) != self._path(expected_document_id):
            raise Stage5EditError("연결된 문서와 다른 네이티브 문서 작업을 차단했습니다.")
        if self.app_type == "excel" and native.sheet != str(context.get("active_container") or ""):
            raise Stage5EditError("연결 후 Excel 시트가 바뀌어 다시 요청해주세요.")
        if self.app_type == "hwp":
            context_has_selection = context.get("selection_kind") == "text"
            native_has_selection = bool(native.current_state.get("has_selection"))
            if context_has_selection != native_has_selection:
                raise Stage5EditError(
                    "한글 선택 영역이 네이티브 작업 준비 과정에서 달라져 실행을 차단했습니다."
                )
            if context_has_selection and int(
                native.current_state.get("selected_length") or 0
            ) != int(context.get("selected_text_length") or 0):
                raise Stage5EditError(
                    "한글 선택 영역 길이가 달라져 다른 텍스트 편집을 차단했습니다."
                )
            if context_has_selection and str(
                native.current_state.get("selected_digest") or ""
            ).upper() != str(context.get("selected_text_digest") or "").upper():
                raise Stage5EditError(
                    "한글 선택 텍스트가 달라져 다른 내용 편집을 차단했습니다."
                )

    @staticmethod
    def _native_before(native: PreparedAction) -> str:
        state = native.current_state
        value = state.get("selected_preview")
        if value is None:
            value = state.get("formula") if state.get("formula") is not None else state.get("value")
        return str(value if value is not None else "")

    @staticmethod
    def _trim_preview(value) -> str:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip()
        return normalized[:240] + ("…" if len(normalized) > 240 else "")

    def prepare(self, request: EditRequest, context: Mapping[str, Any]) -> EditPreparedAction:
        intent = self.analyzer.analyze(
            request.text,
            context,
            selection_reader=self._selection_reader(context),
        )
        if intent.operation not in self.supported_operations:
            raise Stage5EditError(f"허용되지 않은 5단계 작업입니다: {intent.operation}")

        native = None
        estimated_changes = 0
        noop = False
        native_payload = None
        if not intent.read_only:
            native = self.native_adapter.prepare(
                intent.operation,
                self._native_params(intent, context),
            )
            if not isinstance(native, PreparedAction):
                raise Stage5EditError("네이티브 어댑터가 구조화된 작업을 반환하지 않았습니다.")
            self._validate_native_target(native, context)
            native_payload = native.to_dict()
            estimated_changes = max(0, int(native.estimated_changes))
            noop = bool(native.noop)

        before = intent.before_preview or (self._native_before(native) if native else "")
        after = intent.after_preview or intent.description
        preview = {
            "description": intent.description,
            "before": self._trim_preview(before),
            "after": self._trim_preview(after),
            "target": (
                native.target if native else context.get("selection_reference")
            ),
            "estimated_changes": estimated_changes,
            "noop": noop,
        }
        changes_document = not intent.read_only and not noop
        risk = (
            RiskLevel.HIGH
            if changes_document and estimated_changes > 1000
            else RiskLevel.MEDIUM
            if changes_document
            else RiskLevel.LOW
        )
        return EditPreparedAction(
            action_id=f"edit-action-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type=self.app_type,
            operation=intent.operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": native.target if native else None,
            },
            arguments={
                "native_prepared_action": native_payload,
                "preview": preview,
                "read_only": intent.read_only,
            },
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
                *(
                    ({"kind": "native_context_fingerprint", "value": native.context_fingerprint},)
                    if native else ()
                ),
            ),
            risk_level=risk,
            requires_approval=changes_document,
            verification_plan={
                "method": native.verification_method if native else "context_snapshot"
            },
            rollback_plan={
                "strategy": "native_adapter_snapshot_restore" if native else "none"
            },
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": estimated_changes,
                "native_reversible": bool(native.reversible) if native else True,
            },
        )

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if bool(prepared_action.arguments.get("read_only")):
            return {
                "changed": False,
                "verified": True,
                "context": dict(self.get_context()),
            }
        native = PreparedAction.from_dict(
            prepared_action.arguments.get("native_prepared_action") or {}
        )
        result = self.native_adapter.execute(native)
        self._last_native_result = dict(result)
        return dict(result)

    def verify(
        self,
        prepared_action: EditPreparedAction,
        result: Mapping[str, Any],
    ) -> bool:
        return bool(result.get("verified", False))

    def rollback(self, prepared_action: EditPreparedAction) -> bool:
        # ExcelAdapter and HwpAdapter restore their own snapshots before raising.
        # A returned native result is already read-back verified, so no second
        # application-level Undo is safe here.
        if self._last_native_result is not None:
            return False
        payload = prepared_action.arguments.get("native_prepared_action") or {}
        if not payload:
            return True
        try:
            native = PreparedAction.from_dict(payload)
        except Exception:
            return False
        return bool(native.reversible)


def edit_preview_message(prepared: EditPreparedAction) -> str:
    preview = dict(prepared.metadata.get("preview") or {})
    if prepared.operation == "activate_user_preference":
        lines = [
            f"최근 같은 선호를 {prepared.arguments.get('evidence_count', 0)}회 확인했습니다.",
            f"대상: {preview.get('target') or '사용자 선호'}",
            f"작업: {preview.get('description') or '앞으로의 기본값 활성화'}",
        ]
        if preview.get("before"):
            lines.append(f"변경 전: {preview['before']}")
        if preview.get("after"):
            lines.append(f"변경 후: {preview['after']}")
        if preview.get("replacement_candidate"):
            lines.append(
                "승인하면 기존 기본값을 교체하고, 취소하면 기존 기본값을 유지합니다."
            )
        else:
            lines.append("이 기본값을 활성화할까요?")
        return "\n".join(lines)
    target = preview.get("target") or prepared.target.get("selection_reference") or "현재 선택"
    lines = [
        f"대상: {target}",
        f"작업: {preview.get('description') or prepared.operation}",
    ]
    if preview.get("before"):
        lines.append(f"변경 전: {preview['before']}")
    if preview.get("after"):
        lines.append(f"변경 후: {preview['after']}")
    changes = int(preview.get("estimated_changes") or 0)
    if changes:
        lines.append(f"예상 변경: {changes}개")
    lines.append("이 작업을 적용할까요?")
    return "\n".join(lines)


def edit_success_message(prepared: EditPreparedAction, result) -> str:
    preview = dict(prepared.metadata.get("preview") or {})
    if prepared.operation == "record_user_preference_evidence":
        observations = result.observations
        return (
            f"{preview.get('description') or '사용자 선호'} 증거를 "
            f"{observations.get('evidence_count', 0)}회 기록했습니다. "
            "아직 기본값으로 확정하지 않았으며, 같은 패턴이 반복되면 확인을 요청합니다."
        )
    if prepared.operation == "activate_user_preference":
        observations = result.observations
        if observations.get("replaced_previous"):
            return (
                f"{preview.get('description') or '사용자 선호'}을(를) 사용자 승인으로 "
                "교체했습니다. 이전 승인 기본값은 더 이상 적용하지 않고 새 값을 "
                f"사용합니다. 적용 범위: {observations.get('scope_kind', '')}."
            )
        return (
            f"{preview.get('description') or '사용자 선호'}을(를) 사용자 승인으로 "
            f"활성화했습니다. 적용 범위: {observations.get('scope_kind', '')}."
        )
    if prepared.operation == "deactivate_user_preference":
        return f"{preview.get('description') or '사용자 선호'}을(를) 비활성화했습니다."
    if prepared.operation == "activate_business_workflow_skill":
        return (
            "검증된 복합 업무 구조를 사용자 승인으로 재사용 스킬에 활성화했습니다. "
            "다음에 `지난번처럼 해줘`라고 말해도 현재 Excel과 새 산출물 경로를 "
            "다시 검증하고 전체 작업을 다시 승인받습니다."
        )
    if prepared.operation == "deactivate_business_workflow_skill":
        return "승인된 복합 업무 재사용 스킬을 해제했습니다."
    if prepared.operation == "open_recent_workflow_artifact":
        observations = result.observations
        return (
            f"{observations.get('artifact_label') or '최근 검증 산출물'} "
            f"`{observations.get('file_name') or ''}`을(를) 정확한 파일 지문으로 "
            "확인해 열고 문서 창을 맨 앞으로 가져왔습니다."
        )
    if prepared.operation == "connect_recent_workflow_artifact":
        observations = result.observations
        return (
            f"{observations.get('artifact_label') or '최근 검증 산출물'} "
            f"`{observations.get('file_name') or ''}`을(를) 정확한 파일 지문으로 "
            "확인해 열고 새 편집 대상으로 연결했습니다. 다음 편집 명령은 이 "
            "문서의 현재 선택 영역에 적용됩니다."
        )
    if prepared.operation == "inspect_excel_relationships":
        observations = result.observations
        candidates = [
            dict(item)
            for item in (observations.get("candidates") or [])
            if isinstance(item, Mapping)
        ]
        if not candidates:
            return (
                "현재 Excel을 읽기 전용으로 검사했지만 안전 기준을 충족하는 "
                "공통 키 후보를 찾지 못했습니다. 셀 값과 파일 경로는 저장하지 "
                "않았고 Excel 원본도 변경하지 않았습니다."
            )
        cardinality_labels = {
            "one_to_one": "1:1",
            "one_to_many": "1:N",
            "many_to_one": "N:1",
            "many_to_many": "N:M",
        }
        lines = [
            f"현재 Excel에서 조인 키 후보 {len(candidates)}개를 읽기 전용으로 "
            "확인했습니다."
        ]
        for index, candidate in enumerate(candidates, 1):
            flags = []
            if candidate.get("requires_preaggregation"):
                flags.append("양쪽 사전 집계 필요")
            if candidate.get("sample_limited"):
                flags.append("표본 제한")
            if candidate.get("match_basis") == "value_overlap":
                flags.append("열 이름 다름")
            if candidate.get("ambiguous"):
                flags.append("복수 후보와 겹침")
            if candidate.get("confidence") != "high":
                flags.append("사용자 검토 필요")
            suffix = f" · {' · '.join(flags)}" if flags else ""
            left_key = candidate.get("left_key")
            right_key = candidate.get("right_key")
            key_description = (
                str(left_key)
                if left_key == right_key
                else f"{left_key} ↔ {right_key}"
            )
            lines.append(
                f"{index}. {candidate.get('left_sheet')} ↔ "
                f"{candidate.get('right_sheet')} · 키 "
                f"{key_description} · "
                f"{cardinality_labels.get(candidate.get('cardinality'), '?')} · "
                f"겹치는 키 {candidate.get('matched_key_count', 0)}개{suffix}"
            )
        lines.append(
            "셀 값과 파일 경로는 저장하지 않았고 Excel 원본도 변경하지 "
            "않았습니다. 실제 조인은 두 시트·양쪽 키·내부/왼쪽 방식을 말한 뒤 "
            "별도 미리보기를 승인해야 합니다."
        )
        return "\n".join(lines)
    if prepared.operation in {"create_business_workflow", "resume_business_workflow"}:
        observations = result.observations
        outputs = dict(observations.get("output_paths") or {})
        report_format = str(observations.get("report_format") or "word")
        report_label = {
            "word": "Word",
            "hwp": "한글",
            "both": "Word·한글",
        }.get(report_format, "Word")
        if report_format == "both":
            report_paths = (
                f"Word: {outputs.get('report_word', '')}\n"
                f"한글: {outputs.get('report_hwp', '')}"
            )
        else:
            report_paths = f"{report_label}: {outputs.get('report', '')}"
        message = (
            f"Excel 원본을 변경하지 않고 분석을 완료해 {report_label} 보고서와 "
            f"{observations.get('slide_count', 5)}장짜리 PowerPoint를 만들고 "
            "파일 지문까지 검증했습니다.\n"
            f"{report_paths}\n"
            f"PowerPoint: {outputs.get('presentation', '')}\n"
            f"워크플로 ID: {observations.get('workflow_id', '')}"
        )
        candidate = dict(observations.get("workflow_skill_candidate") or {})
        if observations.get("workflow_skill_reused"):
            message += (
                "\n승인된 복합 업무 스킬을 현재 문서에서 새 계획으로 재생하고 "
                "모든 단계를 다시 검증했습니다."
            )
        elif candidate.get("needs_confirmation"):
            message += (
                "\n성공한 단계 구조만 재사용 후보로 준비했습니다. 문서 내용·경로는 "
                "저장하지 않았습니다. `이 워크플로 기억해`라고 말하면 활성화 전에 "
                "다시 확인합니다."
            )
        if observations.get("join_plan"):
            join_plan = dict(observations["join_plan"])
            join_label = (
                "내부"
                if join_plan.get("join_type") == "inner"
                else "왼쪽"
            )
            key_label = str(join_plan.get("left_key") or "")
            if join_plan.get("left_key") != join_plan.get("right_key"):
                key_label += f" ↔ {join_plan.get('right_key')}"
            function_labels = {
                "sum": "합계",
                "average": "평균",
                "count": "건수",
                "minimum": "최솟값",
                "maximum": "최댓값",
            }
            aggregation_parts = []
            for field, sheet in (
                ("left_aggregation", join_plan.get("left_sheet")),
                ("right_aggregation", join_plan.get("right_sheet")),
            ):
                raw_aggregation = join_plan.get(field)
                aggregations = (
                    [dict(raw_aggregation)]
                    if isinstance(raw_aggregation, Mapping)
                    else [
                        dict(item)
                        for item in (raw_aggregation or [])
                        if isinstance(item, Mapping)
                    ]
                )
                aggregation_parts.extend(
                    "{}/{} {}".format(
                        sheet,
                        item.get("column"),
                        function_labels.get(
                            item.get("function"), item.get("function")
                        ),
                    )
                    for item in aggregations
                )
            aggregation_label = (
                f" · {' · '.join(aggregation_parts)} 집계"
                if aggregation_parts
                else ""
            )
            message += (
                f"\n승인한 {join_label} 조인을 읽기 전용으로 검증했습니다: "
                f"{join_plan.get('left_sheet')} ↔ "
                f"{join_plan.get('right_sheet')} · 키 "
                f"{key_label}{aggregation_label}. 시트명과 키는 재사용 스킬로 "
                "저장하지 않았습니다."
            )
        if observations.get("source_scope"):
            source_scope = dict(observations["source_scope"])
            message += (
                "\n승인한 Excel 선택 범위만 읽기 전용으로 분석했습니다: "
                f"{source_scope.get('sheet_name')}!"
                f"{source_scope.get('address')}. 범위 밖 셀은 제외했고, "
                "시트명과 주소는 재사용 스킬로 저장하지 않았습니다."
            )
        return message
    if prepared.operation == "vba_inspect_project":
        observations = result.observations
        modules = observations.get("modules") or []
        names = ", ".join(str(item.get("name")) for item in modules)
        if not observations.get("has_vba_project"):
            return "현재 Excel 통합문서에는 VBA 프로젝트가 없습니다."
        return f"VBA 모듈 {len(modules)}개를 확인했습니다: {names or '이름 없음'}"
    if prepared.operation == "vba_read_module":
        code = str(result.observations.get("code") or "")
        displayed = code[:12_000] + ("\n' … 나머지 코드는 결과 데이터에 있습니다." if len(code) > 12_000 else "")
        return (
            f"{result.observations.get('module_name')} VBA 모듈 코드입니다.\n"
            f"```vba\n{displayed}\n```"
        )
    if prepared.operation == "vba_analyze_module":
        observations = result.observations
        message = str(observations.get("explanation") or "VBA 코드 분석을 완료했습니다.")
        findings = list(observations.get("findings") or [])
        if findings:
            details = "\n".join(
                f"- {item.get('severity')}: {item.get('message')} (줄 {item.get('line')})"
                for item in findings[:10]
            )
            message += "\n점검 결과:\n" + details
        return message
    if prepared.operation == "vba_replace_module":
        backup = result.observations.get("backup_path")
        return (
            f"{prepared.target.get('native_target')} VBA 모듈을 백업한 뒤 수정하고 다시 읽어 확인했습니다."
            + (f" 백업: {backup}" if backup else "")
        )
    if prepared.operation == "vba_run_procedure":
        return (
            f"{result.observations.get('module_name')}."
            f"{result.observations.get('procedure_name')} 호출이 COM 오류 없이 반환됐습니다. "
            "매크로가 만든 셀·파일 등 업무 결과는 별도 후조건이 없어 확인하지 않았습니다."
        )
    if not result.changed:
        if prepared.operation in {"read_selection", "inspect_context"}:
            if prepared.operation == "inspect_context" and preview.get("after"):
                return str(preview["after"])
            context = result.observations.get("context", {})
            selected = context.get("selected_text_preview") or "선택 영역에 표시할 텍스트가 없습니다."
            return f"현재 선택 영역: {selected}"
        return "현재 대상이 이미 요청한 상태라 문서를 변경하지 않았습니다."
    target = preview.get("target") or prepared.target.get("selection_reference") or "현재 선택"
    return f"{target}에 {preview.get('description') or prepared.operation} 작업을 적용하고 다시 읽어 확인했습니다."
