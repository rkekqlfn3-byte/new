"""Highlight Excel cells that meet a numeric condition.

Two operations share this preparation. ``apply_conditional_format`` adds a
FormatConditions rule that keeps re-evaluating; ``format_matching_values``
paints the cells that match right now and nothing afterwards.
"""

from __future__ import annotations

import math

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.operations.excel.base import (
    MAX_DIRECT_FORMAT_CELLS,
    MAX_SOURCE_CELLS,
    ExcelOperation,
)
from engine.app_actions.value_normalizer import (
    excel_values_equal,
    normalize_excel_input,
    serializable_excel_value,
)

XL_CELL_VALUE = 1
XL_NONE = -4142

CONDITION_OPERATORS = {
    "ge": 7,
    "gt": 5,
    "le": 8,
    "lt": 6,
    "eq": 3,
    "ne": 4,
}

COLOR_VALUES = {
    "yellow": 65535,
    "red": 255,
    "green": 5287936,
    "blue": 12611584,
    "orange": 3243501,
    "gray": 12566463,
}


def normalize_threshold(value):
    normalized = normalize_excel_input(value, "number")
    if isinstance(normalized["value"], bool):
        raise AppActionBlocked("서식 기준값은 숫자여야 합니다.")
    return normalized["value"]


def normalize_color(value):
    color = str(value or "yellow").strip().casefold()
    aliases = {
        "노란색": "yellow", "노랑": "yellow",
        "빨간색": "red", "빨강": "red",
        "초록색": "green", "녹색": "green", "초록": "green",
        "파란색": "blue", "파랑": "blue",
        "주황색": "orange", "주황": "orange",
        "회색": "gray", "회색깔": "gray",
    }
    color = aliases.get(color, color)
    if color not in COLOR_VALUES:
        raise AppActionBlocked(
            "지원 색상은 노란색·빨간색·초록색·파란색·주황색·회색입니다."
        )
    return color, COLOR_VALUES[color]


def normalize_operator(value):
    operator = str(value or "").strip().casefold()
    aliases = {
        ">=": "ge", "이상": "ge",
        ">": "gt", "초과": "gt",
        "<=": "le", "이하": "le",
        "<": "lt", "미만": "lt",
        "=": "eq", "==": "eq", "같음": "eq", "동일": "eq",
        "!=": "ne", "<>": "ne", "다름": "ne",
    }
    operator = aliases.get(operator, operator)
    if operator not in CONDITION_OPERATORS:
        raise AppActionBlocked("지원하지 않는 비교 조건입니다.")
    return operator


def condition_true(value, operator, threshold):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if operator == "ge":
        return value >= threshold
    if operator == "gt":
        return value > threshold
    if operator == "le":
        return value <= threshold
    if operator == "lt":
        return value < threshold
    if operator == "eq":
        return excel_values_equal(value, threshold)
    return not excel_values_equal(value, threshold)


def formula_number(value):
    text = str(value or "").strip()
    if text.startswith("="):
        text = text[1:].strip()
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def has_same_condition(descriptors, operator, threshold, color_value):
    expected_operator = CONDITION_OPERATORS[operator]
    for descriptor in descriptors:
        number = formula_number(descriptor.get("formula1"))
        if (
            descriptor.get("type") == XL_CELL_VALUE
            and descriptor.get("operator") == expected_operator
            and number is not None
            and math.isclose(float(number), float(threshold))
            and descriptor.get("color") == color_value
        ):
            return True
    return False


class ConditionFormatOperation(ExcelOperation):
    """Shared preparation. ``persistent`` picks which of the two this is."""

    persistent = True

    def prepare(self, adapter, application, params):
        persistent = self.persistent
        _, sheet, base = adapter._common_context(application)
        source, source_address, header = adapter._resolve_source_range(sheet, params)
        count = adapter._range_count(source)
        if count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"4단계에서는 한 번에 최대 {MAX_SOURCE_CELLS:,}개 셀까지 검사할 수 있습니다."
            )
        operator = normalize_operator(params.get("operator"))
        threshold = normalize_threshold(params.get("threshold"))
        color, color_value = normalize_color(
            params.get("color") or params.get("fill_color")
        )
        descriptors = adapter._condition_descriptors(source)
        snapshot = {
            **base,
            "operation": self.name,
            "target": source_address,
            "source_range": source_address,
            "source_digest": adapter._range_digest(source),
            "source_count": count,
            "operator": operator,
            "threshold": threshold,
            "color": color,
            "condition_rules": descriptors,
        }
        original_formats = []
        matches = []
        if persistent:
            noop = has_same_condition(descriptors, operator, threshold, color_value)
            estimated_changes = 0 if noop else 1
        else:
            for cell in adapter._range_cells(source):
                if condition_true(cell.Value2, operator, threshold):
                    matches.append(adapter._address(cell))
                    original_formats.append({
                        "address": adapter._address(cell),
                        "color": serializable_excel_value(cell.Interior.Color),
                        "color_index": serializable_excel_value(
                            cell.Interior.ColorIndex
                        ),
                    })
            if len(matches) > MAX_DIRECT_FORMAT_CELLS:
                raise AppActionBlocked(
                    f"한 번 표시 방식은 최대 {MAX_DIRECT_FORMAT_CELLS:,}개 셀까지 변경할 수 있습니다. 조건부 서식을 사용해주세요."
                )
            snapshot["matching_cells"] = matches
            snapshot["original_formats"] = original_formats
            noop = not matches
            estimated_changes = len(matches)

        return PreparedAction(
            app=self.app,
            operation=self.name,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=source_address,
            params={
                "source_range": source_address,
                "column_name": header,
                "operator": operator,
                "threshold": threshold,
                "color": color,
                "color_value": color_value,
                "matching_cells": matches,
                "original_formats": original_formats,
            },
            current_state={
                "source_range": source_address,
                "source_count": count,
                "matching_count": len(matches),
                "condition_count": len(descriptors),
            },
            estimated_changes=estimated_changes,
            destructive=False,
            reversible=True,
            verification_method=(
                "read_format_condition" if persistent else "read_cell_fill_colors"
            ),
            context_fingerprint=adapter._state_fingerprint(snapshot),
            prepared_at=adapter._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )


class ApplyConditionalFormatOperation(ConditionFormatOperation):
    name = "apply_conditional_format"
    persistent = True

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        source = sheet.Range(current.target)
        before = {"condition_count": current.current_state["condition_count"]}
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        condition = None
        try:
            condition = source.FormatConditions.Add(
                Type=XL_CELL_VALUE,
                Operator=CONDITION_OPERATORS[current.params["operator"]],
                Formula1=str(current.params["threshold"]),
            )
            condition.Interior.Color = current.params["color_value"]
            descriptors = adapter._condition_descriptors(source)
            if not has_same_condition(
                descriptors,
                current.params["operator"],
                current.params["threshold"],
                current.params["color_value"],
            ):
                raise AppActionVerificationError(
                    "추가한 Excel 조건부 서식을 다시 찾지 못했습니다."
                )
            after = {"condition_count": len(descriptors)}
        except Exception as error:
            try:
                if condition is not None:
                    condition.Delete()
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 조건부 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)


class FormatMatchingValuesOperation(ConditionFormatOperation):
    name = "format_matching_values"
    persistent = False

    def execute(self, adapter, application, prepared):
        current = self.prepare(adapter, application, prepared.params)
        adapter._ensure_same_context(current, prepared)
        _, sheet, _ = adapter._common_context(application)
        originals = current.params["original_formats"]
        before = {"matching_count": len(originals)}
        if current.noop:
            return adapter._result(current, before, before, changed=False)
        changed_cells = []
        try:
            for original in originals:
                cell = sheet.Range(original["address"])
                cell.Interior.Color = current.params["color_value"]
                changed_cells.append((cell, original))
            for cell, _ in changed_cells:
                if int(cell.Interior.Color) != int(current.params["color_value"]):
                    raise AppActionVerificationError(
                        f"Excel {adapter._address(cell)} 셀의 채우기 색을 확인하지 못했습니다."
                    )
            after = {
                "matching_count": len(changed_cells),
                "color": current.params["color"],
            }
        except Exception as error:
            for cell, original in reversed(changed_cells):
                try:
                    if original.get("color_index") == XL_NONE:
                        cell.Interior.ColorIndex = XL_NONE
                    else:
                        cell.Interior.Color = original.get("color")
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 셀 표시 또는 검증에 실패했습니다."
            ) from error
        return adapter._result(current, before, after, changed=True)


__all__ = [
    "COLOR_VALUES",
    "CONDITION_OPERATORS",
    "ApplyConditionalFormatOperation",
    "FormatMatchingValuesOperation",
    "condition_true",
    "has_same_condition",
    "normalize_color",
    "normalize_operator",
    "normalize_threshold",
]
