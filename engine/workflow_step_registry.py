"""Content-free allowlist for reusable cross-application workflow steps."""

from __future__ import annotations

import copy
from typing import Any, Mapping


WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION = 1

_STEP_DEFINITIONS = {
    "analyze_excel": {
        "effect": "read_only_analysis",
        "completion_evidence": "validated_common_model",
    },
    "create_word_report": {
        "effect": "create_owned_file",
        "completion_evidence": "file_fingerprint_and_word_readback",
    },
    "create_hwp_report": {
        "effect": "create_owned_file",
        "completion_evidence": "file_fingerprint_and_hwp_readback",
    },
    "create_powerpoint_summary": {
        "effect": "create_owned_file",
        "completion_evidence": "file_fingerprint_and_powerpoint_readback",
    },
}

_REPORT_STEP_ORDERS = {
    "word": (
        "analyze_excel",
        "create_word_report",
        "create_powerpoint_summary",
    ),
    "hwp": (
        "analyze_excel",
        "create_hwp_report",
        "create_powerpoint_summary",
    ),
    "both": (
        "analyze_excel",
        "create_word_report",
        "create_hwp_report",
        "create_powerpoint_summary",
    ),
}


def registered_workflow_step_names() -> tuple[str, ...]:
    """Return stable allowlisted step IDs without exposing mutable registry data."""
    return tuple(_STEP_DEFINITIONS)


def report_workflow_step_order(
    report_format,
    include_presentation=True,
    include_report=True,
) -> tuple[str, ...]:
    normalized = str(report_format or "").strip().casefold()
    try:
        order = _REPORT_STEP_ORDERS[normalized]
    except KeyError as error:
        raise ValueError("지원하지 않는 보고서 워크플로 형식입니다.") from error
    if type(include_presentation) is not bool:
        raise ValueError("발표자료 포함 여부는 참/거짓이어야 합니다.")
    if type(include_report) is not bool:
        raise ValueError("보고서 포함 여부는 참/거짓이어야 합니다.")
    if not include_report and not include_presentation:
        raise ValueError("보고서와 발표자료를 모두 제외할 수 없습니다.")
    if not include_report:
        return ("analyze_excel", "create_powerpoint_summary")
    if include_presentation:
        return order
    if (
        not order
        or order[-1] != "create_powerpoint_summary"
        or order.count("create_powerpoint_summary") != 1
    ):
        raise RuntimeError("보고서 워크플로의 발표자료 단계 위치가 올바르지 않습니다.")
    return order[:-1]


def report_workflow_step_recipe(
    report_format,
    include_presentation=True,
    include_report=True,
) -> list[dict[str, Any]]:
    """Build a path- and content-free, sequential recipe from registered steps."""
    recipe = []
    previous = None
    for step_name in report_workflow_step_order(
        report_format,
        include_presentation,
        include_report,
    ):
        definition = _STEP_DEFINITIONS[step_name]
        recipe.append({
            "registry_schema_version": WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION,
            "step_name": step_name,
            "depends_on": [] if previous is None else [previous],
            "effect": definition["effect"],
            "completion_evidence": definition["completion_evidence"],
        })
        previous = step_name
    return recipe


def validate_report_workflow_step_recipe(
    value,
    report_format,
    include_presentation=True,
    include_report=True,
) -> list[dict[str, Any]]:
    """Accept only the exact registered recipe for one supported report format."""
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError("워크플로 단계 레시피가 JSON 목록이 아닙니다.")
    expected = report_workflow_step_recipe(
        report_format,
        include_presentation,
        include_report,
    )
    if value != expected:
        raise ValueError("워크플로 단계 레시피가 허용 목록과 다릅니다.")
    return copy.deepcopy(expected)
