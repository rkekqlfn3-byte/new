"""Stage 10: resumable Excel-to-report-and-PowerPoint business workflows."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import multiprocessing
import os
import queue
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from engine.learning import validate_preference_value
from engine.runtime_paths import USER_DATA_DIR
from engine.storage.json_store import atomic_write_json, safe_read_json
from engine.workflow_step_registry import (
    WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION,
    registered_workflow_step_names,
    report_workflow_step_order,
    report_workflow_step_recipe,
)
from engine.workflows.excel_analysis_services import (
    ExcelWorkbookAnalysisService,
    PivotInsightService,
    RelationshipAnalysisService,
    RelationshipCandidateExportService,
)
from engine.workflows.workflow_execution_services import (
    WorkflowPlanPreparationService,
    WorkflowStepRunService,
)
from engine.workflows.workflow_join_services import (
    ExplicitJoinService,
    JoinPlanValidator,
)

WORKFLOW_SCHEMA_VERSION = 6
WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION = 1
STEP_NAMES = registered_workflow_step_names()
EXECUTABLE_STEP_NAMES = frozenset({
    "analyze_excel",
    "create_word_report",
    "create_hwp_report",
    "create_powerpoint_summary",
})
if frozenset(STEP_NAMES) != EXECUTABLE_STEP_NAMES:
    raise RuntimeError("워크플로 단계 레지스트리와 실행기 허용 목록이 다릅니다.")
MAX_WORKSHEETS = 20
MAX_SOURCE_CELLS_PER_SHEET = 50_000
MAX_SOURCE_CELLS = 100_000
MAX_TABLE_ROWS = 100
MAX_TOTAL_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 30
MAX_METRICS = 50
MAX_INSIGHTS = 50
MAX_CHARTS = 20
MAX_RELATIONSHIPS = 10
MAX_RELATION_VALUES = 5_000
RELATIONSHIP_CANDIDATE_FIELDS = frozenset({
    "left_sheet",
    "right_sheet",
    "left_key",
    "right_key",
    "match_basis",
    "ambiguous",
    "cardinality",
    "matched_key_count",
    "left_distinct_count",
    "right_distinct_count",
    "left_coverage",
    "right_coverage",
    "sample_limited",
    "requires_preaggregation",
    "confidence",
})
MAX_PIVOT_SUMMARIES = 10
MAX_PIVOT_GROUPS = 5
MAX_JOIN_SOURCE_ROWS = 5_000
MAX_JOIN_OUTPUT_ROWS = 5_000
MAX_WORK_PRODUCT_BYTES = 1_000_000
SUPPORTED_EXCEL_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls"})
HWP_AUTOMATION_GUIDE_URL = "https://developer.hancom.com/hwpautomation"
HWP_AUTOMATION_MODULE_REGISTRY = (
    r"HKCU\Software\HNC\HwpAutomation\Modules"
)
REPORT_FORMATS = {
    "word": {"label": "Word", "suffix": ".docx"},
    "hwp": {"label": "한글", "suffix": ".hwp"},
    "both": {"label": "Word·한글", "suffix": None},
}
WORKFLOW_PREFERENCE_KEYS = frozenset({
    "summary_lines", "report_tone", "title_style", "number_format",
    "table_style", "ppt_slide_count", "preferred_output_dir",
    "confirmation_actions", "workflow_order", "emphasis_style",
    "font_scale", "paragraph_align", "_learning_metadata",
})


class WorkflowError(RuntimeError):
    """The requested workflow is unsafe, invalid, or unavailable."""


class WorkflowExecutionError(WorkflowError):
    """One persistent workflow step failed and can be resumed."""

    error_type = "execution_error"
    status = "failed"
    retryable = True

    def __init__(
        self,
        workflow_id: str,
        step: str,
        message: str,
        *,
        cause=None,
    ):
        self.workflow_id = str(workflow_id)
        self.step = str(step)
        self.failed_step = self.step
        cause_error_type = str(
            getattr(cause, "error_type", "") or ""
        ).strip()
        if cause_error_type:
            self.error_type = cause_error_type
        cause_status = str(getattr(cause, "status", "") or "").strip()
        if cause_status:
            self.status = cause_status
        if hasattr(cause, "retryable"):
            self.retryable = bool(cause.retryable)
        self.diagnostic_context = {
            "workflow_hash": hashlib.sha256(
                self.workflow_id.encode("utf-8")
            ).hexdigest().upper(),
            "failed_step": self.failed_step,
            "cause_error_type": self.error_type,
        }
        cause_context = getattr(cause, "diagnostic_context", None)
        if isinstance(cause_context, Mapping):
            self.diagnostic_context.update(dict(cause_context))
        super().__init__(
            f"워크플로 {self.workflow_id}의 {self.step} 단계에서 실패했습니다: {message} "
            "같은 Excel 문서에서 '실패한 워크플로 이어서'라고 요청하면 이 단계부터 다시 시도합니다."
        )


class HwpWorkflowTimeout(WorkflowError):
    """A Jarvis-owned HWP worker exceeded its bounded execution window."""

    error_type = "timeout"
    status = "failed"
    retryable = True


class HwpSecurityModuleUnavailable(WorkflowError):
    """HWP file access cannot proceed without a user-installed security module."""

    error_type = "environment_error"
    status = "blocked"
    retryable = True

    def __init__(self, message: str):
        self.diagnostic_context = {
            "environment_component": "hwp_automation_security_module",
            "setup_guide_url": HWP_AUTOMATION_GUIDE_URL,
            "registry_location": HWP_AUTOMATION_MODULE_REGISTRY,
            "automatic_install_attempted": False,
        }
        super().__init__(message)


class WorkflowJoinValidationError(WorkflowError):
    """An explicit row join is ambiguous, unsafe, or outside bounded limits."""

    error_type = "validation_error"
    status = "blocked"
    retryable = False


class WorkflowSourceScopeValidationError(WorkflowError):
    """An explicit Excel source range is missing, ambiguous, or unsafe."""

    error_type = "validation_error"
    status = "blocked"
    retryable = False


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _absolute_path(value) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(value)))


def _path_key(value) -> str:
    return os.path.normcase(_absolute_path(value))


def _json_value(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return str(value)


def _bounded_list(value, label: str, limit: int) -> list:
    if not isinstance(value, (list, tuple)):
        raise WorkflowError(f"공통 업무 데이터의 {label} 항목은 목록이어야 합니다.")
    if len(value) > limit:
        raise WorkflowError(f"공통 업무 데이터의 {label} 항목은 최대 {limit}개까지 지원합니다.")
    return [_json_value(item) for item in value]


@dataclass(frozen=True)
class WorkProductData:
    """COM-free common data passed between the three Stage 10 steps."""

    title: str
    metrics: list
    tables: list
    charts: list
    insights: list
    source_files: list

    def __post_init__(self):
        title = str(self.title or "").strip()
        if not title:
            raise WorkflowError("공통 업무 데이터에는 제목이 필요합니다.")
        if len(title) > 300:
            raise WorkflowError("공통 업무 데이터의 제목이 너무 깁니다.")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "metrics", _bounded_list(self.metrics, "metrics", MAX_METRICS))
        object.__setattr__(self, "tables", _bounded_list(self.tables, "tables", 20))
        object.__setattr__(self, "charts", _bounded_list(self.charts, "charts", MAX_CHARTS))
        object.__setattr__(self, "insights", _bounded_list(self.insights, "insights", MAX_INSIGHTS))
        sources = _bounded_list(self.source_files, "source_files", 20)
        object.__setattr__(self, "source_files", [str(item) for item in sources])
        encoded = json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_WORK_PRODUCT_BYTES:
            raise WorkflowError("공통 업무 데이터가 1MB 안전 한도를 넘었습니다.")

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))

    @classmethod
    def from_value(cls, value: Mapping[str, Any] | "WorkProductData") -> "WorkProductData":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise WorkflowError("공통 업무 데이터가 JSON 객체가 아닙니다.")
        required = {"title", "metrics", "tables", "charts", "insights", "source_files"}
        missing = sorted(required - set(value))
        if missing:
            raise WorkflowError("공통 업무 데이터가 불완전합니다: " + ", ".join(missing))
        return cls(**{key: value[key] for key in required})


def file_fingerprint(path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise WorkflowError(f"파일을 찾을 수 없습니다: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = source.stat()
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": digest.hexdigest().upper(),
    }


def _fingerprint_matches(path, expected) -> bool:
    try:
        actual = file_fingerprint(path)
    except WorkflowError:
        return False
    reference = dict(expected or {})
    return bool(reference) and all(actual.get(key) == reference.get(key) for key in actual)


def _safe_stem(value: str) -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", str(value or "")).strip(" .")
    return (text[:80] or "Excel_분석")


def _reserve_output(directory: Path, stem: str, suffix: str, token: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    return directory / f"{stem}_{token[:8]}{suffix}"


def _report_format(value) -> str:
    normalized = str(value or "word").strip().casefold()
    aliases = {
        "docx": "word",
        "워드": "word",
        "한글": "hwp",
        "hwpx": "hwp",
        "word+hwp": "both",
        "word_hwp": "both",
        "word·한글": "both",
        "워드+한글": "both",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in REPORT_FORMATS:
        raise WorkflowError("보고서 형식은 Word, 한글 또는 두 형식이어야 합니다.")
    return normalized


def _report_kinds(report_format) -> tuple[str, ...]:
    normalized = _report_format(report_format)
    return ("word", "hwp") if normalized == "both" else (normalized,)


def _include_presentation(value) -> bool:
    if type(value) is not bool:
        raise WorkflowError("발표자료 포함 여부는 참/거짓이어야 합니다.")
    return value


def _include_report(value) -> bool:
    if type(value) is not bool:
        raise WorkflowError("보고서 포함 여부는 참/거짓이어야 합니다.")
    return value


def _step_order(
    report_format,
    include_presentation=True,
    include_report=True,
) -> tuple[str, ...]:
    normalized = _report_format(report_format)
    try:
        return report_workflow_step_order(
            normalized,
            _include_presentation(include_presentation),
            _include_report(include_report),
        )
    except ValueError as error:
        raise WorkflowError(str(error)) from error


def _workflow_step_contracts(
    workflow_id,
    report_format,
    source_fingerprint,
    include_presentation=True,
    include_report=True,
) -> dict[str, dict[str, Any]]:
    """Build deterministic, content-free execution contracts for every step."""
    source_sha256 = str(
        (source_fingerprint or {}).get("sha256")
        if isinstance(source_fingerprint, Mapping)
        else ""
    ).strip().upper()
    if not re.fullmatch(r"[A-F0-9]{64}", source_sha256):
        raise WorkflowError("워크플로 원본 지문이 올바르지 않습니다.")
    contracts = {}
    for definition in report_workflow_step_recipe(
        _report_format(report_format),
        _include_presentation(include_presentation),
        _include_report(include_report),
    ):
        step_name = definition["step_name"]
        depends_on = list(definition["depends_on"])
        identity = {
            "schema_version": WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION,
            "workflow_id": str(workflow_id or ""),
            "step_name": step_name,
            "depends_on": depends_on,
            "source_sha256": source_sha256,
        }
        idempotency_key = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper()
        contracts[step_name] = {
            "schema_version": WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION,
            "step_name": step_name,
            "depends_on": depends_on,
            "effect": definition["effect"],
            "completion_evidence": definition["completion_evidence"],
            "idempotency_key": idempotency_key,
        }
    return contracts


def _validated_state_step_contracts(
    state: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Fail closed unless the stored contracts exactly match the approved plan."""
    expected = _workflow_step_contracts(
        state.get("workflow_id"),
        state.get("report_format") or "word",
        state.get("source_fingerprint"),
        state.get("include_presentation"),
        state.get("include_report"),
    )
    try:
        schema_version = int(state.get("step_contract_schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if (
        schema_version != WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION
        or state.get("step_contracts") != expected
    ):
        raise WorkflowError("워크플로 단계 실행 계약이 올바르지 않습니다.")
    return expected


def _expected_output_suffixes(
    report_format,
    include_presentation=True,
    include_report=True,
) -> dict[str, str]:
    normalized = _report_format(report_format)
    include_presentation = _include_presentation(include_presentation)
    include_report = _include_report(include_report)
    if not include_report and not include_presentation:
        raise WorkflowError("보고서와 발표자료를 모두 제외할 수 없습니다.")
    if not include_report:
        expected = {}
    elif normalized == "both":
        expected = {"report_word": ".docx", "report_hwp": ".hwp"}
    else:
        expected = {"report": REPORT_FORMATS[normalized]["suffix"]}
    if include_presentation:
        expected["presentation"] = ".pptx"
    return expected


def _report_output_key(report_format, kind: str) -> str:
    normalized = _report_format(report_format)
    if kind not in _report_kinds(normalized):
        raise WorkflowError("워크플로에 요청되지 않은 보고서 단계입니다.")
    return f"report_{kind}" if normalized == "both" else "report"


def _format_number(value, style="plain") -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return str(value)
    clean = int(number) if number.is_integer() else number
    if style == "currency_krw":
        return f"₩{clean:,.0f}"
    if style == "percent":
        return f"{number * 100:,.1f}%"
    if style == "thousands":
        return f"{clean:,}"
    return str(clean)


def _validated_workflow_preferences(value) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise WorkflowError("워크플로 기본값은 JSON 객체여야 합니다.")
    unknown = set(value) - WORKFLOW_PREFERENCE_KEYS
    if unknown:
        raise WorkflowError("허용되지 않은 워크플로 기본값이 있습니다.")
    raw = dict(value)
    metadata = raw.pop("_learning_metadata", None)
    result = {
        name: validate_preference_value(name, preference_value)
        for name, preference_value in raw.items()
    }
    if metadata is not None:
        if not isinstance(metadata, Mapping):
            raise WorkflowError("학습 기본값 출처 정보가 올바르지 않습니다.")
        result["_learning_metadata"] = _json_value(dict(metadata))
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 100_000:
        raise WorkflowError("워크플로 기본값 정보가 너무 큽니다.")
    return result


def _validated_source_scope(value) -> dict[str, str] | None:
    """Validate one content-free, exact Excel range analysis scope."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "kind", "sheet_name", "address"
    }:
        raise WorkflowSourceScopeValidationError(
            "선택 범위 계획에는 범위 종류·시트명·셀 주소만 있어야 합니다."
        )
    if str(value.get("kind") or "").strip().casefold() != "range":
        raise WorkflowSourceScopeValidationError(
            "현재 워크플로 분석 범위는 Excel 셀 범위만 지원합니다."
        )
    sheet_name = re.sub(
        r"\s+", " ", str(value.get("sheet_name") or "")
    ).strip()
    if (
        not sheet_name
        or len(sheet_name) > 80
        or any(ord(character) < 32 for character in sheet_name)
    ):
        raise WorkflowSourceScopeValidationError(
            "분석할 시트명은 1~80자의 한 줄 이름이어야 합니다."
        )
    address = re.sub(
        r"\s+", "", str(value.get("address") or "")
    ).replace("$", "").upper()
    match = re.fullmatch(
        r"(?P<start_col>[A-Z]{1,3})(?P<start_row>[1-9]\d*)"
        r"(?::(?P<end_col>[A-Z]{1,3})(?P<end_row>[1-9]\d*))?",
        address,
    )
    if match is None:
        raise WorkflowSourceScopeValidationError(
            "분석할 선택 범위는 A1:E20 같은 연속 셀 주소여야 합니다."
        )

    def column_number(letters):
        result = 0
        for character in letters:
            result = result * 26 + ord(character) - ord("A") + 1
        return result

    start_column = column_number(match.group("start_col"))
    end_column = column_number(
        match.group("end_col") or match.group("start_col")
    )
    start_row = int(match.group("start_row"))
    end_row = int(match.group("end_row") or match.group("start_row"))
    if (
        start_column > end_column
        or start_row > end_row
        or end_column > 16_384
        or end_row > 1_048_576
    ):
        raise WorkflowSourceScopeValidationError(
            "선택 범위의 시작·끝 주소가 Excel 범위를 벗어났습니다."
        )
    rows = end_row - start_row + 1
    columns = end_column - start_column + 1
    if rows < 2:
        raise WorkflowSourceScopeValidationError(
            "선택 범위는 머리글 1행과 데이터 1행 이상을 포함해야 합니다."
        )
    if columns > MAX_TABLE_COLUMNS:
        raise WorkflowSourceScopeValidationError(
            f"선택 범위는 {MAX_TABLE_COLUMNS}열까지 분석할 수 있습니다."
        )
    if rows * columns > MAX_SOURCE_CELLS_PER_SHEET:
        raise WorkflowSourceScopeValidationError(
            f"선택 범위는 {MAX_SOURCE_CELLS_PER_SHEET:,}셀까지 분석할 수 있습니다."
        )
    normalized_end = (
        f":{match.group('end_col')}{end_row}"
        if match.group("end_col")
        else ""
    )
    return {
        "kind": "range",
        "sheet_name": sheet_name,
        "address": (
            f"{match.group('start_col')}{start_row}{normalized_end}"
        ),
    }


def _validated_join_plan(value) -> dict[str, Any] | None:
    return JoinPlanValidator(WorkflowJoinValidationError).validate(value)


def _join_aggregation_items(plan, side) -> list[dict[str, str]]:
    value = dict(plan or {}).get(f"{side}_aggregation")
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value]
    return []


class ExcelSalesAnalyzer:
    """Read the exact workbook without changing it and produce bounded JSON data."""

    def __init__(self, application_getter=None, application_factory=None, com_runtime=None):
        self._application_getter = application_getter
        self._application_factory = application_factory
        self._com_runtime = com_runtime

    @staticmethod
    def _default_getter():
        import win32com.client

        return win32com.client.GetActiveObject("Excel.Application")

    @staticmethod
    def _default_factory(program_id):
        import win32com.client

        return win32com.client.DispatchEx(program_id)

    @staticmethod
    def _workbook_path(workbook) -> str:
        return _absolute_path(str(getattr(workbook, "FullName", "") or ""))

    def _find_workbook(self, application, source_path: str):
        workbooks = application.Workbooks
        for index in range(1, int(workbooks.Count) + 1):
            workbook = workbooks.Item(index)
            if _path_key(self._workbook_path(workbook)) == _path_key(source_path):
                return workbook
        return None

    def _open(self, source_path: str):
        from engine.app_actions.com_lifecycle import (
            OfficeApplicationLease,
            application_lease,
        )

        getter = self._application_getter or self._default_getter
        try:
            lease = application_lease(getter(), "excel")
            workbook = self._find_workbook(lease.application, source_path)
            if workbook is not None:
                return lease, workbook
            lease.cleanup()
        except Exception:
            pass
        factory = self._application_factory or self._default_factory
        application = factory("Excel.Application")
        lease = OfficeApplicationLease(application, True, "excel")
        try:
            application.Visible = False
            application.DisplayAlerts = False
            workbook = application.Workbooks.Open(
                source_path,
                UpdateLinks=0,
                ReadOnly=True,
                IgnoreReadOnlyRecommended=True,
                AddToMru=False,
            )
            lease.register_owned_document(workbook)
            return lease, workbook
        except Exception:
            lease.cleanup()
            raise

    @staticmethod
    def _matrix(value, rows: int, columns: int) -> list[list[Any]]:
        if rows == 1 and columns == 1:
            return [[_json_value(value)]]
        if rows == 1:
            source = list(value) if isinstance(value, (tuple, list)) else [value]
            if source and isinstance(source[0], (tuple, list)):
                source = list(source[0])
            return [[_json_value(item) for item in source[:columns]]]
        source_rows = list(value) if isinstance(value, (tuple, list)) else [[value]]
        result = []
        for source_row in source_rows[:rows]:
            row = list(source_row) if isinstance(source_row, (tuple, list)) else [source_row]
            result.append([_json_value(item) for item in row[:columns]])
        return result

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _chart_data(worksheet, limit=MAX_CHARTS) -> list[dict[str, Any]]:
        charts = []
        try:
            objects = worksheet.ChartObjects()
            count = min(int(objects.Count), max(0, int(limit)))
            for index in range(1, count + 1):
                chart = objects.Item(index).Chart
                title = ""
                try:
                    if bool(chart.HasTitle):
                        title = str(chart.ChartTitle.Text or "")
                except Exception:
                    pass
                charts.append({
                    "name": str(getattr(objects.Item(index), "Name", "") or f"Chart {index}"),
                    "title": title,
                    "chart_type": int(getattr(chart, "ChartType", 0) or 0),
                    "sheet_name": str(getattr(worksheet, "Name", "") or ""),
                })
        except Exception:
            pass
        return charts

    @staticmethod
    def _header_key(value) -> str:
        return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()

    @classmethod
    def _looks_like_key_header(cls, value) -> bool:
        key = cls._header_key(value)
        return bool(
            key in {"id", "key", "no", "번호", "코드", "식별자"}
            or key.endswith(("id", "key", "번호", "코드"))
        )

    @classmethod
    def _looks_like_measure_header(cls, value) -> bool:
        key = cls._header_key(value)
        return any(
            token in key
            for token in (
                "amount", "revenue", "sales", "cost", "quantity", "score",
                "total", "value", "금액", "매출", "비용", "수량", "점수",
                "합계", "평균", "단가", "값",
            )
        )

    @classmethod
    def _relation_column_profile(cls, rows, column: int) -> dict[str, Any]:
        values = []
        for row in rows:
            value = row[column] if column < len(row) else None
            normalized = cls._join_key_value(value)
            if normalized is None:
                continue
            values.append(hashlib.sha256(
                normalized[:500].encode("utf-8")
            ).hexdigest())
            if len(values) >= MAX_RELATION_VALUES:
                break
        unique = set(values)
        return {
            "values": unique,
            "nonempty_count": len(values),
            "unique_count": len(unique),
            "sample_limited": len(values) >= MAX_RELATION_VALUES,
        }

    @classmethod
    def _join_key_value(cls, value) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            # Text identifiers such as "001" intentionally remain distinct
            # from numeric 1. Plain numeric text such as "1" still matches
            # Excel's numeric Value2 representation.
            if re.fullmatch(r"[+-]?0\d+(?:\.\d+)?", text):
                return f"text:{text.casefold()[:500]}"
        number = cls._number(value)
        if number is not None:
            return f"number:{number:.15g}"
        text = str(value).strip().casefold()
        return f"text:{text[:500]}" if text else None

    @classmethod
    def _apply_explicit_join(
        cls,
        profiles,
        tables,
        join_plan,
        *,
        remaining_table_rows: int,
    ) -> str:
        limits = {
            "worksheets": MAX_WORKSHEETS,
            "source_rows": MAX_JOIN_SOURCE_ROWS,
            "table_columns": MAX_TABLE_COLUMNS,
            "table_rows": MAX_TABLE_ROWS,
            "output_rows": MAX_JOIN_OUTPUT_ROWS,
        }
        return ExplicitJoinService(
            cls,
            _validated_join_plan,
            WorkflowJoinValidationError,
            limits,
        ).execute(
            profiles,
            tables,
            join_plan,
            remaining_table_rows=remaining_table_rows,
        )

    @classmethod
    def _relationship_insights(cls, profiles) -> list[str]:
        return RelationshipAnalysisService(
            cls, MAX_RELATIONSHIPS
        ).apply(profiles)

    def relationship_candidates(
        self,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        return RelationshipCandidateExportService(
            self, MAX_RELATIONSHIPS
        ).build(context)

    @classmethod
    def _pivot_insights(cls, profiles) -> list[str]:
        return PivotInsightService(
            cls,
            _format_number,
            MAX_PIVOT_SUMMARIES,
            MAX_PIVOT_GROUPS,
        ).apply(profiles)

    def run(self, context: Mapping[str, Any]) -> dict[str, Any]:
        dependencies = {
            "absolute_path": _absolute_path,
            "path_key": _path_key,
            "validate_join": _validated_join_plan,
            "validate_scope": _validated_source_scope,
            "error": WorkflowError,
            "scope_error": WorkflowSourceScopeValidationError,
            "product": WorkProductData,
        }
        limits = {
            "worksheets": MAX_WORKSHEETS,
            "sheet_cells": MAX_SOURCE_CELLS_PER_SHEET,
            "source_cells": MAX_SOURCE_CELLS,
            "table_columns": MAX_TABLE_COLUMNS,
            "table_rows": MAX_TABLE_ROWS,
            "total_table_rows": MAX_TOTAL_TABLE_ROWS,
            "metrics": MAX_METRICS,
            "charts": MAX_CHARTS,
        }
        return ExcelWorkbookAnalysisService(
            self, dependencies, limits
        ).run(context)


class WordReportWriter:
    """Create one new DOCX report in an owned Word instance."""

    def __init__(self, application_factory=None, com_runtime=None):
        self._application_factory = application_factory
        self._com_runtime = com_runtime

    @staticmethod
    def _default_factory(program_id):
        import win32com.client

        return win32com.client.DispatchEx(program_id)

    @staticmethod
    def _report_text(product: WorkProductData, preferences=None) -> str:
        preferences = dict(preferences or {})
        number_style = str(preferences.get("number_format") or "plain")
        tone = str(preferences.get("report_tone") or "formal")
        table_style = str(preferences.get("table_style") or "basic")
        intro = {
            "formal": "본 보고서는 Excel 원본을 읽기 전용으로 분석한 결과입니다.",
            "concise": "Excel 분석 결과 요약",
            "friendly": "Excel 자료에서 확인한 핵심 내용을 정리했습니다.",
        }.get(tone, "Excel 분석 결과 요약")
        lines = [product.title, intro, "", "핵심 지표"]
        for metric in product.metrics:
            lines.append(
                f"- {metric.get('name')}: 합계 {_format_number(metric.get('sum'), number_style)}, "
                f"평균 {_format_number(metric.get('average'), number_style)}, "
                f"최소 {_format_number(metric.get('minimum'), number_style)}, "
                f"최대 {_format_number(metric.get('maximum'), number_style)}"
            )
        lines.extend(["", "분석 인사이트"])
        lines.extend(f"- {item}" for item in product.insights)
        lines.extend(["", "표 요약"])
        for table in product.tables:
            headers = " | ".join(str(item) for item in table.get("headers", []))
            if table_style == "header_bold":
                headers = f"[머리글] {headers}"
            elif table_style == "banded":
                headers = f"[줄무늬 표] {headers}"
            lines.append(f"{table.get('name')}: {table.get('total_rows', 0)}행")
            if headers:
                lines.append(headers)
            for row in list(table.get("rows") or [])[:30]:
                lines.append(" | ".join(
                    "" if item is None else _format_number(item, number_style)
                    for item in row
                ))
        if product.charts:
            lines.extend(["", "원본 차트"])
            for chart in product.charts:
                lines.append(
                    f"- {chart.get('title') or chart.get('name') or '이름 없는 차트'} "
                    f"(형식 {chart.get('chart_type', '알 수 없음')})"
                )
        lines.extend(["", "원본 파일"])
        lines.extend(f"- {Path(item).name}" for item in product.source_files)
        return "\r\n".join(lines)[:200_000]

    @staticmethod
    def _formatting_plan(preferences=None) -> dict[str, Any]:
        """Translate approved semantic defaults to bounded Word values."""
        preferences = dict(preferences or {})
        plan = {}
        emphasis = preferences.get("emphasis_style")
        if emphasis is not None:
            if emphasis not in {"bold", "regular"}:
                raise WorkflowError("보고서 굵기 기본값이 올바르지 않습니다.")
            plan["emphasis_style"] = emphasis
            plan["bold"] = emphasis == "bold"
        scale = preferences.get("font_scale")
        if scale is not None:
            if scale not in {"larger", "smaller"}:
                raise WorkflowError("보고서 글자 크기 기본값이 올바르지 않습니다.")
            plan["font_scale"] = scale
            plan["font_size"] = 14.0 if scale == "larger" else 10.0
        alignment = preferences.get("paragraph_align")
        if alignment is not None:
            alignments = {"left": 0, "center": 1, "right": 2, "justify": 3}
            if alignment not in alignments:
                raise WorkflowError("보고서 문단 정렬 기본값이 올바르지 않습니다.")
            plan["paragraph_align"] = alignment
            plan["paragraph_alignment"] = alignments[alignment]
        return plan

    @classmethod
    def _apply_formatting_preferences(cls, content, preferences=None) -> dict[str, Any]:
        """Apply and read back only approved whole-report formatting defaults."""
        plan = cls._formatting_plan(preferences)
        if "bold" in plan:
            content.Font.Bold = -1 if plan["bold"] else 0
            actual = int(content.Font.Bold)
            if bool(actual) != bool(plan["bold"]):
                raise WorkflowError("Word 보고서 굵기 기본값 검증에 실패했습니다.")
        if "font_size" in plan:
            content.Font.Size = float(plan["font_size"])
            if abs(float(content.Font.Size) - float(plan["font_size"])) > 0.01:
                raise WorkflowError("Word 보고서 글자 크기 기본값 검증에 실패했습니다.")
        if "paragraph_alignment" in plan:
            content.ParagraphFormat.Alignment = int(plan["paragraph_alignment"])
            if int(content.ParagraphFormat.Alignment) != int(
                plan["paragraph_alignment"]
            ):
                raise WorkflowError("Word 보고서 문단 정렬 기본값 검증에 실패했습니다.")
        return {
            key: plan[key]
            for key in ("emphasis_style", "font_scale", "paragraph_align")
            if key in plan
        }

    def run(self, context: Mapping[str, Any], work_product: Mapping[str, Any]) -> dict[str, Any]:
        from engine.app_actions.com_lifecycle import OfficeApplicationLease, com_apartment

        output_path = Path(context["output_path"])
        if output_path.exists():
            raise WorkflowError("기존 Word 파일을 덮어쓰지 않습니다.")
        product = WorkProductData.from_value(work_product)
        factory = self._application_factory or self._default_factory
        lease = None
        created = False
        with com_apartment(self._com_runtime):
            try:
                application = factory("Word.Application")
                lease = OfficeApplicationLease(application, True, "word")
                application.Visible = False
                application.DisplayAlerts = 0
                document = lease.register_owned_document(application.Documents.Add())
                document.Content.Text = self._report_text(
                    product, context.get("preferences")
                )
                applied_formatting = self._apply_formatting_preferences(
                    document.Content,
                    context.get("preferences"),
                )
                document.SaveAs2(str(output_path), FileFormat=16, AddToRecentFiles=False)
                created = output_path.is_file()
                if not created or int(document.Paragraphs.Count) < 1:
                    raise WorkflowError("Word 보고서 저장 후 검증에 실패했습니다.")
                return {
                    "path": str(output_path),
                    "fingerprint": file_fingerprint(output_path),
                    "verification": {
                        "exists": True,
                        "format": "docx",
                        "paragraph_count": int(document.Paragraphs.Count),
                        "applied_formatting": applied_formatting,
                    },
                }
            except Exception:
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass
                raise
            finally:
                if lease is not None:
                    lease.cleanup()


HWP_PROCESS_NAMES = frozenset({"hwp.exe", "hwp64.exe"})
DEFAULT_HWP_WORKFLOW_TIMEOUT_SECONDS = 60.0


def _hwp_process_ids() -> set[int]:
    try:
        import psutil
    except ImportError:
        return set()
    result = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if str(process.info.get("name") or "").casefold() in HWP_PROCESS_NAMES:
                result.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def _registered_hwp_security_module() -> str | None:
    """Return one valid user-configured HWP Automation security module name."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\HNC\HwpAutomation\Modules",
            0,
            winreg.KEY_READ,
        )
    except OSError:
        return None
    candidates = []
    try:
        value_count = int(winreg.QueryInfoKey(key)[1])
        for index in range(value_count):
            try:
                name, raw_path, value_type = winreg.EnumValue(key, index)
            except OSError:
                continue
            if value_type not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}:
                continue
            module_name = str(name or "").strip()
            module_path = os.path.expandvars(str(raw_path or "").strip())
            if module_name and module_path and Path(module_path).is_file():
                candidates.append(module_name)
    finally:
        winreg.CloseKey(key)
    return sorted(candidates, key=str.casefold)[0] if candidates else None


def _stop_owned_hwp_process(process_id: int, baseline: set[int]) -> bool:
    process_id = int(process_id or 0)
    if process_id <= 0 or process_id in set(baseline or set()):
        return False
    try:
        import psutil

        process = psutil.Process(process_id)
        if str(process.name() or "").casefold() not in HWP_PROCESS_NAMES:
            return False
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
        return False


def _queue_latest_nowait(values):
    latest = None
    while True:
        try:
            latest = values.get_nowait()
        except queue.Empty:
            return latest


class HwpReportWriter:
    """Create one new HWP report in a Jarvis-owned HwpObject."""

    def __init__(
        self,
        application_factory=None,
        com_runtime=None,
        *,
        timeout_seconds=None,
        process_context_factory=None,
        process_ids=None,
        process_stopper=None,
        security_module_resolver=None,
        _worker_mode=False,
        _ownership_reporter=None,
        _security_module_name=None,
    ):
        self._application_factory = application_factory
        self._com_runtime = com_runtime
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get(
                "JARVIS_HWP_WORKFLOW_TIMEOUT_SECONDS",
                DEFAULT_HWP_WORKFLOW_TIMEOUT_SECONDS,
            )
        )
        try:
            configured_timeout = float(configured_timeout)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "한글 보고서 생성 제한 시간은 숫자여야 합니다."
            ) from error
        self._timeout_seconds = max(5.0, min(configured_timeout, 300.0))
        self._process_context_factory = (
            process_context_factory
            or (lambda: multiprocessing.get_context("spawn"))
        )
        self._process_ids = process_ids or _hwp_process_ids
        self._process_stopper = process_stopper or _stop_owned_hwp_process
        self._security_module_resolver = (
            security_module_resolver
            or _registered_hwp_security_module
        )
        self._worker_mode = bool(_worker_mode)
        self._ownership_reporter = _ownership_reporter
        self._security_module_name = (
            str(_security_module_name or "").strip() or None
        )

    @staticmethod
    def _formatting_plan(preferences=None) -> dict[str, Any]:
        plan = WordReportWriter._formatting_plan(preferences)
        alignments = {"justify": 0, "left": 1, "right": 2, "center": 3}
        if "paragraph_align" in plan:
            plan["hwp_paragraph_alignment"] = alignments[plan["paragraph_align"]]
        return plan

    @classmethod
    def _apply_formatting_preferences(cls, hwp, preferences=None) -> dict[str, Any]:
        plan = cls._formatting_plan(preferences)
        if not plan:
            return {}
        hwp.HAction.Run("SelectAll")
        if "bold" in plan or "font_size" in plan:
            shape = hwp.HParameterSet.HCharShape
            hwp.HAction.GetDefault("CharShape", shape.HSet)
            if "bold" in plan:
                shape.Bold = 1 if plan["bold"] else 0
            if "font_size" in plan:
                shape.Height = int(hwp.PointToHwpUnit(plan["font_size"]))
            hwp.HAction.Execute("CharShape", shape.HSet)
            hwp.HAction.GetDefault("CharShape", shape.HSet)
            if "bold" in plan and bool(int(shape.Bold)) != bool(plan["bold"]):
                raise WorkflowError("한글 보고서 굵기 기본값 검증에 실패했습니다.")
            if "font_size" in plan:
                expected_height = int(hwp.PointToHwpUnit(plan["font_size"]))
                if int(shape.Height) != expected_height:
                    raise WorkflowError("한글 보고서 글자 크기 기본값 검증에 실패했습니다.")
        if "hwp_paragraph_alignment" in plan:
            actions = {
                "justify": "ParagraphShapeAlignJustify",
                "left": "ParagraphShapeAlignLeft",
                "right": "ParagraphShapeAlignRight",
                "center": "ParagraphShapeAlignCenter",
            }
            hwp.HAction.Run(actions[plan["paragraph_align"]])
            paragraph = hwp.HParameterSet.HParaShape
            hwp.HAction.GetDefault("ParagraphShape", paragraph.HSet)
            if int(paragraph.AlignType) != int(plan["hwp_paragraph_alignment"]):
                raise WorkflowError("한글 보고서 문단 정렬 기본값 검증에 실패했습니다.")
        return {
            key: plan[key]
            for key in ("emphasis_style", "font_scale", "paragraph_align")
            if key in plan
        }

    @staticmethod
    def _validated_output_path(context: Mapping[str, Any]) -> Path:
        output_path = Path(context["output_path"])
        if output_path.exists():
            raise WorkflowError("기존 한글 파일을 덮어쓰지 않습니다.")
        if output_path.suffix.casefold() != ".hwp":
            raise WorkflowError("한글 보고서 출력 경로는 .hwp 형식이어야 합니다.")
        return output_path

    def preflight(self) -> dict[str, Any]:
        """Verify that the HWP save environment is ready before approval."""
        if (
            self._worker_mode
            or self._application_factory is not None
            or self._com_runtime is not None
        ):
            return {
                "status": "injected",
                "security_module_name": self._security_module_name,
            }
        security_module_name = self._security_module_resolver()
        if not security_module_name:
            raise HwpSecurityModuleUnavailable(
                "한글 Automation 파일 보안 모듈이 등록되어 있지 않아 보고서를 "
                "저장할 수 없습니다. 한컴 개발 가이드의 '보안모듈(Automation).zip'"
                "을 사용자가 설치·등록한 뒤 다시 미리보기를 요청해주세요: "
                f"{HWP_AUTOMATION_GUIDE_URL}"
            )
        return {
            "status": "ready",
            "security_module_name": str(security_module_name),
        }

    def run(
        self,
        context: Mapping[str, Any],
        work_product: Mapping[str, Any],
    ) -> dict[str, Any]:
        output_path = self._validated_output_path(context)
        product = WorkProductData.from_value(work_product).to_dict()
        preflight = self.preflight()
        if (
            self._worker_mode
            or self._application_factory is not None
            or self._com_runtime is not None
        ):
            return self._run_inline(context, product)
        security_module_name = str(preflight["security_module_name"])
        return self._run_isolated(
            context,
            product,
            output_path,
            security_module_name,
        )

    def _run_isolated(
        self,
        context: Mapping[str, Any],
        work_product: Mapping[str, Any],
        output_path: Path,
        security_module_name: str,
    ) -> dict[str, Any]:
        baseline = set(self._process_ids())
        process_context = self._process_context_factory()
        result_queue = process_context.Queue(maxsize=1)
        ownership_queue = process_context.Queue(maxsize=2)
        process = process_context.Process(
            target=_hwp_report_worker,
            args=(
                dict(context),
                dict(work_product),
                tuple(sorted(baseline)),
                str(security_module_name),
                result_queue,
                ownership_queue,
            ),
        )
        process.start()
        process.join(self._timeout_seconds)
        owned_process_id = _queue_latest_nowait(ownership_queue)
        try:
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                if owned_process_id:
                    self._process_stopper(
                        int(owned_process_id),
                        baseline,
                    )
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass
                raise HwpWorkflowTimeout(
                    "한글 보고서 저장 응답이 "
                    f"{self._timeout_seconds:g}초 안에 끝나지 않았습니다. "
                    "한글의 파일 접근 확인 창 또는 보안 모듈 상태를 확인한 뒤 "
                    "'실패한 워크플로 이어서'로 다시 시도해주세요."
                )
            try:
                payload = result_queue.get(timeout=2)
            except queue.Empty as error:
                raise WorkflowError(
                    "한글 보고서 생성 프로세스가 결과 없이 종료되었습니다."
                ) from error
            if owned_process_id:
                self._process_stopper(int(owned_process_id), baseline)
            if payload.get("success"):
                return dict(payload["result"])
            message = str(
                payload.get("message")
                or "한글 보고서 생성 프로세스가 실패했습니다."
            )
            error_type = str(payload.get("error_type") or "")
            if error_type == "timeout":
                raise HwpWorkflowTimeout(message)
            if error_type == "environment_error":
                raise HwpSecurityModuleUnavailable(message)
            raise WorkflowError(message)
        finally:
            for values in (result_queue, ownership_queue):
                try:
                    values.close()
                    values.join_thread()
                except Exception:
                    pass

    def _run_inline(
        self,
        context: Mapping[str, Any],
        work_product: Mapping[str, Any],
    ) -> dict[str, Any]:
        from engine.app_actions.com_lifecycle import OfficeApplicationLease, com_apartment
        from engine.app_actions.hwp_adapter import create_owned_hwp_application

        output_path = self._validated_output_path(context)
        product = WorkProductData.from_value(work_product)
        lease = None
        hwp = None
        with com_apartment(self._com_runtime):
            try:
                if self._application_factory is None:
                    lease = create_owned_hwp_application()
                else:
                    application = self._application_factory("HWPFrame.HwpObject")
                    lease = OfficeApplicationLease(application, True, "hwp")
                hwp = lease.application
                if callable(self._ownership_reporter):
                    self._ownership_reporter()
                if self._security_module_name and not bool(
                    hwp.RegisterModule(
                        "FilePathCheckDLL",
                        self._security_module_name,
                    )
                ):
                    raise HwpSecurityModuleUnavailable(
                        "등록된 한글 Automation 파일 보안 모듈을 활성화하지 "
                        "못해 보고서를 저장하지 않았습니다."
                    )
                report_text = WordReportWriter._report_text(
                    product, context.get("preferences")
                )
                parameter = hwp.HParameterSet.HInsertText
                hwp.HAction.GetDefault("InsertText", parameter.HSet)
                parameter.Text = report_text
                hwp.HAction.Execute("InsertText", parameter.HSet)
                applied_formatting = self._apply_formatting_preferences(
                    hwp, context.get("preferences")
                )
                actual_text = str(hwp.GetTextFile("UNICODE", "") or "")
                if product.title not in actual_text or len(actual_text) < len(product.title):
                    raise WorkflowError("한글 보고서 내용 재읽기 검증에 실패했습니다.")
                if not bool(hwp.SaveAs(str(output_path), "HWP", "")):
                    raise WorkflowError("한글 보고서를 저장하지 못했습니다.")
                if not output_path.is_file():
                    raise WorkflowError("한글 보고서 저장 후 파일을 찾을 수 없습니다.")
                return {
                    "path": str(output_path),
                    "fingerprint": file_fingerprint(output_path),
                    "verification": {
                        "exists": True,
                        "format": "hwp",
                        "content_readback": True,
                        "title_present": True,
                        "text_length": len(actual_text),
                        "content_digest": hashlib.sha256(
                            actual_text.encode("utf-8")
                        ).hexdigest().upper(),
                        "applied_formatting": applied_formatting,
                    },
                }
            except Exception:
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass
                raise
            finally:
                if hwp is not None:
                    try:
                        hwp.Clear(1)
                    except Exception:
                        pass
                hwp = None
                if lease is not None:
                    lease.cleanup()


def _hwp_report_worker(
    context,
    work_product,
    baseline_process_ids,
    security_module_name,
    result_queue,
    ownership_queue,
):
    """Generate one HWP artifact in an isolated process and return JSON data."""
    baseline = set(int(value) for value in baseline_process_ids)

    def report_owned_process():
        created = _hwp_process_ids() - baseline
        if len(created) == 1:
            ownership_queue.put_nowait(next(iter(created)))

    writer = HwpReportWriter(
        _worker_mode=True,
        _ownership_reporter=report_owned_process,
        _security_module_name=security_module_name,
    )
    try:
        result_queue.put({
            "success": True,
            "result": writer.run(context, work_product),
        })
    except BaseException as error:
        result_queue.put({
            "success": False,
            "error_type": str(
                getattr(error, "error_type", "execution_error")
            ),
            "message": str(error)[:2_000],
        })


class PowerPointSummaryWriter:
    """Create exactly five summary slides in an owned PowerPoint instance."""

    def __init__(self, application_factory=None, com_runtime=None):
        self._application_factory = application_factory
        self._com_runtime = com_runtime

    @staticmethod
    def _default_factory(program_id):
        import win32com.client

        return win32com.client.DispatchEx(program_id)

    @staticmethod
    def _formatting_plan(preferences=None, *, role="body") -> dict[str, Any]:
        preferences = dict(preferences or {})
        plan = {}
        emphasis = preferences.get("emphasis_style")
        if emphasis is not None:
            emphasis = validate_preference_value("emphasis_style", emphasis)
            plan["bold"] = emphasis == "bold"
            plan["emphasis_style"] = emphasis
        scale = preferences.get("font_scale")
        if scale is not None:
            scale = validate_preference_value("font_scale", scale)
            sizes = {
                "larger": {"title": 36.0, "body": 24.0},
                "smaller": {"title": 24.0, "body": 14.0},
            }
            plan["font_size"] = sizes[scale]["title" if role == "title" else "body"]
            plan["font_scale"] = scale
        alignment = preferences.get("paragraph_align")
        if alignment is not None:
            alignment = validate_preference_value("paragraph_align", alignment)
            plan["paragraph_alignment"] = {
                "left": 1,
                "center": 2,
                "right": 3,
                "justify": 4,
            }[alignment]
            plan["paragraph_align"] = alignment
        return plan

    @classmethod
    def _apply_formatting_preferences(
        cls,
        text_range,
        preferences=None,
        *,
        role="body",
    ) -> dict[str, Any]:
        plan = cls._formatting_plan(preferences, role=role)
        if "bold" in plan:
            text_range.Font.Bold = -1 if plan["bold"] else 0
        if "font_size" in plan:
            text_range.Font.Size = float(plan["font_size"])
        if "paragraph_alignment" in plan:
            text_range.ParagraphFormat.Alignment = int(
                plan["paragraph_alignment"]
            )
        if "bold" in plan and bool(int(text_range.Font.Bold)) != plan["bold"]:
            raise WorkflowError("PowerPoint 굵기 기본값 검증에 실패했습니다.")
        if "font_size" in plan and abs(
            float(text_range.Font.Size) - float(plan["font_size"])
        ) > 0.01:
            raise WorkflowError("PowerPoint 글자 크기 기본값 검증에 실패했습니다.")
        if "paragraph_alignment" in plan and int(
            text_range.ParagraphFormat.Alignment
        ) != int(plan["paragraph_alignment"]):
            raise WorkflowError("PowerPoint 문단 정렬 기본값 검증에 실패했습니다.")
        return {
            key: plan[key]
            for key in ("emphasis_style", "font_scale", "paragraph_align")
            if key in plan
        }

    @staticmethod
    def _slide_content(
        product: WorkProductData,
        preferences=None,
        slide_count=5,
        report_label="Word",
    ) -> list[tuple[str, str]]:
        preferences = dict(preferences or {})
        number_style = str(preferences.get("number_format") or "plain")
        source = Path(product.source_files[0]).name if product.source_files else ""
        metrics = "\r\n".join(
            f"• {item.get('name')}: 합계 {_format_number(item.get('sum'), number_style)} / "
            f"평균 {_format_number(item.get('average'), number_style)}"
            for item in product.metrics[:8]
        ) or "• 숫자형 핵심 지표 없음"
        tables = product.tables
        table = tables[0] if tables else {}
        sheet_names = ", ".join(
            str(item.get("name") or "-") for item in tables[:10]
        )
        total_rows = sum(int(item.get("total_rows") or 0) for item in tables)
        table_summary = (
            f"• 분석 시트: {len(tables)}개 ({sheet_names or '-'})\r\n"
            f"• 전체 데이터 행: {total_rows:,}\r\n"
            f"• 주요 열: {', '.join(str(item) for item in list(table.get('headers') or [])[:10])}"
        )
        insights = "\r\n".join(f"• {item}" for item in product.insights[:8])
        charts = product.charts
        if charts:
            insights += "\r\n" + f"• 원본 차트 {len(charts)}개 확인"
        conclusion = (
            ("• " + str(product.insights[0])) if product.insights else "• 원본 표 구조를 확인했습니다."
        ) + f"\r\n• 상세 수치와 표는 함께 생성된 {report_label} 보고서를 확인하세요."
        base = [
            (product.title, f"Excel 분석 요약\r\n원본: {source}"),
            ("핵심 지표", metrics),
            ("데이터 구성", table_summary),
            ("주요 인사이트", insights or "• 추가 인사이트 없음"),
            ("결론 및 다음 단계", conclusion),
        ]
        slide_count = max(3, min(int(slide_count), 20))
        if slide_count == 3:
            return [base[0], base[3], base[4]]
        if slide_count == 4:
            return [base[0], base[1], base[3], base[4]]
        if slide_count == 5:
            return base
        details = []
        for index in range(slide_count - 5):
            metric = product.metrics[index % len(product.metrics)] if product.metrics else {}
            details.append((
                f"세부 지표 {index + 1}",
                (
                    f"• 항목: {metric.get('name', '추가 분석')}\r\n"
                    f"• 합계: {_format_number(metric.get('sum'), number_style)}\r\n"
                    f"• 평균: {_format_number(metric.get('average'), number_style)}"
                ),
            ))
        return base[:-1] + details + base[-1:]

    def run(self, context: Mapping[str, Any], work_product: Mapping[str, Any]) -> dict[str, Any]:
        from engine.app_actions.com_lifecycle import OfficeApplicationLease, com_apartment

        output_path = Path(context["output_path"])
        if output_path.exists():
            raise WorkflowError("기존 PowerPoint 파일을 덮어쓰지 않습니다.")
        product = WorkProductData.from_value(work_product)
        factory = self._application_factory or self._default_factory
        lease = None
        created = False
        with com_apartment(self._com_runtime):
            try:
                application = factory("PowerPoint.Application")
                lease = OfficeApplicationLease(application, True, "powerpoint")
                application.Visible = True
                presentation = lease.register_owned_document(application.Presentations.Add())
                expected_slides = max(3, min(int(context.get("slide_count") or 5), 20))
                applied_formatting = {}
                formatted_text_range_count = 0
                for index, (title, body) in enumerate(
                    self._slide_content(
                        product,
                        context.get("preferences"),
                        expected_slides,
                        REPORT_FORMATS[_report_format(context.get("report_format"))]["label"],
                    ),
                    1,
                ):
                    layout = 1 if index == 1 else 2  # ppLayoutTitle / ppLayoutText
                    slide = presentation.Slides.Add(index, layout)
                    title_range = slide.Shapes.Title.TextFrame.TextRange
                    body_range = (
                        slide.Shapes.Placeholders.Item(2).TextFrame.TextRange
                    )
                    title_range.Text = str(title)[:500]
                    body_range.Text = str(body)[:5_000]
                    for role, text_range in (
                        ("title", title_range),
                        ("body", body_range),
                    ):
                        applied = self._apply_formatting_preferences(
                            text_range,
                            context.get("preferences"),
                            role=role,
                        )
                        if applied:
                            applied_formatting = applied
                            formatted_text_range_count += 1
                if int(presentation.Slides.Count) != expected_slides:
                    raise WorkflowError(
                        f"PowerPoint가 요청된 {expected_slides}장으로 생성되지 않았습니다."
                    )
                presentation.SaveAs(str(output_path), 24)  # ppSaveAsOpenXMLPresentation
                created = output_path.is_file()
                if not created:
                    raise WorkflowError("PowerPoint 저장 후 파일을 찾을 수 없습니다.")
                return {
                    "path": str(output_path),
                    "fingerprint": file_fingerprint(output_path),
                    "verification": {
                        "exists": True,
                        "format": "pptx",
                        "slide_count": int(presentation.Slides.Count),
                        "expected_slide_count": expected_slides,
                        "applied_formatting": applied_formatting,
                        "formatted_text_range_count": formatted_text_range_count,
                    },
                }
            except Exception:
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass
                raise
            finally:
                if lease is not None:
                    lease.cleanup()


class WorkflowExecutor:
    """Persist every step and resume from the first unverified failure."""

    def __init__(
        self,
        store_dir=None,
        *,
        analyzer=None,
        word_writer=None,
        hwp_writer=None,
        powerpoint_writer=None,
    ):
        self.store_dir = Path(store_dir or (Path(USER_DATA_DIR) / "workflows"))
        self.analyzer = analyzer or ExcelSalesAnalyzer()
        self.word_writer = word_writer or WordReportWriter()
        self.hwp_writer = hwp_writer or HwpReportWriter()
        self.powerpoint_writer = powerpoint_writer or PowerPointSummaryWriter()
        self._relationship_candidate_cache = None

    def remember_relationship_candidates(
        self,
        *,
        source_path,
        source_fingerprint,
        edit_session_id,
        candidates,
    ) -> None:
        """Keep schema-only candidates in memory for one short edit session."""
        raw_candidates = list(candidates or [])
        if len(raw_candidates) > MAX_RELATIONSHIPS:
            raise WorkflowJoinValidationError(
                f"메모리 관계 후보는 최대 {MAX_RELATIONSHIPS}개여야 합니다."
            )
        safe_candidates = []
        for item in raw_candidates:
            if (
                not isinstance(item, Mapping)
                or set(item) != RELATIONSHIP_CANDIDATE_FIELDS
            ):
                raise WorkflowJoinValidationError(
                    "메모리에 보관할 관계 후보 형식이 올바르지 않습니다."
                )
            safe_candidates.append(copy.deepcopy(dict(item)))
        self._relationship_candidate_cache = {
            "source_identity_hash": hashlib.sha256(
                os.path.normcase(_absolute_path(source_path)).encode("utf-8")
            ).hexdigest(),
            "source_fingerprint": copy.deepcopy(dict(source_fingerprint or {})),
            "edit_session_id": str(edit_session_id or ""),
            "captured_at": time.monotonic(),
            "candidates": safe_candidates,
        }

    def resolve_relationship_candidate(
        self,
        *,
        source_path,
        source_fingerprint,
        edit_session_id,
        candidate_index,
        max_age_seconds=600,
    ) -> dict[str, Any]:
        """Resolve one numbered candidate only while its source is unchanged."""
        cache = self._relationship_candidate_cache
        if not isinstance(cache, Mapping):
            raise WorkflowJoinValidationError(
                "먼저 '시트 관계 후보 찾아줘'로 현재 Excel의 후보를 확인해주세요."
            )
        try:
            index = int(candidate_index)
        except (TypeError, ValueError) as error:
            raise WorkflowJoinValidationError(
                "관계 후보 번호는 1~10 사이 숫자여야 합니다."
            ) from error
        candidates = list(cache.get("candidates") or [])
        expired = (
            time.monotonic() - float(cache.get("captured_at") or 0.0)
            > max(1, int(max_age_seconds))
        )
        source_changed = (
            hashlib.sha256(
                os.path.normcase(_absolute_path(source_path)).encode("utf-8")
            ).hexdigest()
            != str(cache.get("source_identity_hash") or "")
            or dict(source_fingerprint or {})
            != dict(cache.get("source_fingerprint") or {})
            or str(edit_session_id or "")
            != str(cache.get("edit_session_id") or "")
        )
        if expired or source_changed:
            self._relationship_candidate_cache = None
            raise WorkflowJoinValidationError(
                "관계 후보를 확인한 뒤 시간이 지났거나 Excel 파일·편집 세션이 "
                "바뀌었습니다. 후보를 다시 찾아주세요."
            )
        if not 1 <= index <= len(candidates):
            raise WorkflowJoinValidationError(
                f"현재 관계 후보는 {len(candidates)}개입니다. 표시된 번호를 "
                "다시 선택해주세요."
            )
        return copy.deepcopy(dict(candidates[index - 1]))

    def _preflight_report_environment(
        self,
        report_format: str,
        include_report=True,
    ) -> None:
        if (
            not _include_report(include_report)
            or "hwp" not in _report_kinds(report_format)
        ):
            return
        preflight = getattr(self.hwp_writer, "preflight", None)
        if callable(preflight):
            preflight()

    def _path(self, workflow_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", str(workflow_id or "")):
            raise WorkflowError("워크플로 ID가 올바르지 않습니다.")
        return self.store_dir / f"{workflow_id}.json"

    def _save(self, state: dict) -> dict:
        state["updated_at"] = _timestamp()
        atomic_write_json(self._path(state["workflow_id"]), state, max_versions=2)
        return state

    def load(self, workflow_id: str) -> dict:
        state = safe_read_json(self._path(workflow_id), None)
        if not isinstance(state, dict):
            raise WorkflowError("저장된 워크플로를 찾을 수 없습니다.")
        if str(state.get("workflow_id") or "") != str(workflow_id):
            raise WorkflowError("저장된 워크플로 ID가 파일 ID와 일치하지 않습니다.")
        schema_version = int(state.get("schema_version") or 0)
        if schema_version not in {1, 2, 3, 4, 5, WORKFLOW_SCHEMA_VERSION}:
            raise WorkflowError("지원하지 않는 워크플로 저장 형식입니다.")
        if schema_version < WORKFLOW_SCHEMA_VERSION:
            state = copy.deepcopy(state)
            report_format = _report_format(state.get("report_format") or "word")
            if schema_version in {1, 2} and report_format == "hwp":
                steps = dict(state.get("steps") or {})
                legacy = steps.pop("create_word_report", None)
                if legacy is not None:
                    steps["create_hwp_report"] = legacy
                state["steps"] = steps
                for key in ("successful_steps",):
                    state[key] = [
                        "create_hwp_report" if item == "create_word_report" else item
                        for item in list(state.get(key) or [])
                    ]
                for key in ("current_step", "failed_step"):
                    if state.get(key) == "create_word_report":
                        state[key] = "create_hwp_report"
                verification = dict(state.get("verification_results") or {})
                if "create_word_report" in verification:
                    verification["create_hwp_report"] = verification.pop(
                        "create_word_report"
                    )
                state["verification_results"] = verification
            state["schema_version"] = WORKFLOW_SCHEMA_VERSION
            state["migrated_from_schema"] = schema_version
            state["report_format"] = report_format
            if schema_version < 5:
                state["include_presentation"] = True
            elif type(state.get("include_presentation")) is not bool:
                raise WorkflowError(
                    "저장된 워크플로의 발표자료 포함 계약이 불완전합니다."
                )
            state["include_report"] = True
            state["step_order"] = list(_step_order(
                report_format,
                state["include_presentation"],
                True,
            ))
        elif type(state.get("include_presentation")) is not bool:
            raise WorkflowError(
                "저장된 워크플로의 발표자료 포함 계약이 불완전합니다."
            )
        elif type(state.get("include_report")) is not bool:
            raise WorkflowError(
                "저장된 워크플로의 보고서 포함 계약이 불완전합니다."
            )
        state.setdefault("report_format", "word")
        state.setdefault(
            "step_order",
            list(_step_order(
                state.get("report_format") or "word",
                state.get("include_presentation"),
                state.get("include_report"),
            )),
        )
        state.setdefault("join_plan", None)
        state.setdefault("source_scope", None)
        has_contract_version = "step_contract_schema_version" in state
        has_contracts = "step_contracts" in state
        if (
            not has_contract_version
            and not has_contracts
            and schema_version in {1, 2, 3}
        ):
            state["step_contract_schema_version"] = (
                WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION
            )
            state["step_contracts"] = _workflow_step_contracts(
                state.get("workflow_id"),
                state.get("report_format") or "word",
                state.get("source_fingerprint"),
                state.get("include_presentation"),
                state.get("include_report"),
            )
        elif not (has_contract_version and has_contracts):
            raise WorkflowError("저장된 워크플로 단계 실행 계약이 불완전합니다.")
        return state

    def prepare(
        self,
        source_path,
        *,
        title=None,
        output_dir=None,
        preferences=None,
        slide_count=5,
        explicit_slide_count=False,
        report_format="word",
        include_presentation=True,
        include_report=True,
        join_plan=None,
        source_scope=None,
    ) -> dict:
        dependencies = {
            "absolute_path": _absolute_path,
            "excel_suffixes": SUPPORTED_EXCEL_SUFFIXES,
            "error": WorkflowError,
            "scope_error": WorkflowSourceScopeValidationError,
            "validate_preferences": _validated_workflow_preferences,
            "report_format": _report_format,
            "include_presentation": _include_presentation,
            "include_report": _include_report,
            "validate_join": _validated_join_plan,
            "validate_scope": _validated_source_scope,
            "report_kinds": _report_kinds,
            "report_output_key": _report_output_key,
            "safe_stem": _safe_stem,
            "reserve_output": _reserve_output,
            "report_formats": REPORT_FORMATS,
            "fingerprint": file_fingerprint,
            "step_order": _step_order,
            "timestamp": _timestamp,
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "contract_schema_version": WORKFLOW_STEP_CONTRACT_SCHEMA_VERSION,
            "step_contracts": _workflow_step_contracts,
        }
        return WorkflowPlanPreparationService(
            self, dependencies
        ).prepare(
            source_path,
            title=title,
            output_dir=output_dir,
            preferences=preferences,
            slide_count=slide_count,
            explicit_slide_count=explicit_slide_count,
            report_format=report_format,
            include_presentation=include_presentation,
            include_report=include_report,
            join_plan=join_plan,
            source_scope=source_scope,
        )

    def _validated_start_plan(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise WorkflowError("승인된 워크플로 계획이 JSON 객체가 아닙니다.")
        state = copy.deepcopy(dict(value))
        if int(state.get("schema_version") or 0) != WORKFLOW_SCHEMA_VERSION:
            raise WorkflowError("승인된 워크플로 계획의 저장 형식이 올바르지 않습니다.")
        workflow_id = str(state.get("workflow_id") or "")
        path = self._path(workflow_id)
        if path.exists():
            raise WorkflowError("이미 시작된 워크플로 계획은 다시 실행할 수 없습니다.")
        if state.get("status") != "approval_required":
            raise WorkflowError("승인 대기 상태의 새 워크플로만 시작할 수 있습니다.")
        if state.get("created_files") or state.get("work_product") is not None:
            raise WorkflowError("실행 전 워크플로 계획에 산출물 상태가 포함돼 있습니다.")
        try:
            state["applied_preferences"] = _validated_workflow_preferences(
                state.get("applied_preferences") or {}
            )
        except (ValueError, TypeError, OSError) as error:
            raise WorkflowError("승인 전에 학습 기본값이 바뀌어 새 미리보기가 필요합니다.") from error

        source = Path(_absolute_path(state.get("source_path") or ""))
        if (
            not source.is_file()
            or source.suffix.casefold() not in SUPPORTED_EXCEL_SUFFIXES
            or not _fingerprint_matches(source, state.get("source_fingerprint"))
        ):
            raise WorkflowError(
                "승인 전에 Excel 원본 파일이 바뀌어 새 미리보기가 필요합니다."
            )
        destination = Path(_absolute_path(state.get("output_dir") or ""))
        if not destination.is_dir():
            raise WorkflowError("승인 전에 산출물 폴더를 찾을 수 없게 됐습니다.")

        report_format = _report_format(state.get("report_format") or "word")
        state["report_format"] = report_format
        include_presentation = _include_presentation(
            state.get("include_presentation")
        )
        state["include_presentation"] = include_presentation
        include_report = _include_report(state.get("include_report"))
        state["include_report"] = include_report
        if not include_report and not include_presentation:
            raise WorkflowError("보고서와 발표자료를 모두 제외할 수 없습니다.")
        if not include_report and report_format != "word":
            raise WorkflowError(
                "발표자료 전용 계획의 내부 보고서 형식이 올바르지 않습니다."
            )
        state["join_plan"] = _validated_join_plan(state.get("join_plan"))
        state["source_scope"] = _validated_source_scope(
            state.get("source_scope")
        )
        if state["join_plan"] is not None and state["source_scope"] is not None:
            raise WorkflowSourceScopeValidationError(
                "승인된 계획에서 시트 조인과 선택 범위 분석이 충돌합니다."
            )
        expected_steps = _step_order(
            report_format,
            include_presentation,
            include_report,
        )
        if (
            tuple(state.get("step_order") or ()) != expected_steps
            or set(state.get("steps") or {}) != set(expected_steps)
        ):
            raise WorkflowError("워크플로 단계 구성이 올바르지 않습니다.")
        state["step_order"] = list(expected_steps)
        _validated_state_step_contracts(state)
        self._preflight_report_environment(report_format, include_report)
        outputs = dict(state.get("output_paths") or {})
        expected = _expected_output_suffixes(
            report_format,
            include_presentation,
            include_report,
        )
        if set(outputs) != set(expected):
            raise WorkflowError("워크플로 산출물 계획이 불완전합니다.")
        normalized_outputs = {}
        for name, suffix in expected.items():
            output = Path(_absolute_path(outputs[name]))
            if (
                _path_key(output.parent) != _path_key(destination)
                or output.suffix.casefold() != suffix
                or output.exists()
            ):
                raise WorkflowError(
                    "승인 전에 산출물 경로가 바뀌거나 같은 이름의 파일이 생겼습니다."
                )
            normalized_outputs[name] = str(output)

        state["source_path"] = str(source)
        state["output_dir"] = str(destination)
        state["output_paths"] = normalized_outputs
        return state

    def start(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """Persist and execute a new workflow only after user approval."""
        state = self._validated_start_plan(plan)
        state["status"] = "running"
        state["approved_at"] = _timestamp()
        self._save(state)
        return self.run(state["workflow_id"])

    def cleanup_stale_previews(self, *, max_age_days=7) -> int:
        """Remove legacy preview-only states that can never be resumed."""
        try:
            max_age_seconds = max(0, int(max_age_days)) * 24 * 60 * 60
        except (TypeError, ValueError) as error:
            raise WorkflowError("워크플로 보존 기간이 올바르지 않습니다.") from error
        if not self.store_dir.is_dir():
            return 0
        removed = 0
        now = time.time()
        for path in self.store_dir.glob("*.json"):
            state = safe_read_json(path, None, recover=False)
            if not isinstance(state, dict) or state.get("status") != "approval_required":
                continue
            try:
                old_enough = now - path.stat().st_mtime >= max_age_seconds
            except OSError:
                continue
            if not old_enough:
                continue
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            try:
                Path(f"{path}.bak").unlink()
            except FileNotFoundError:
                pass
            backup_dir = path.parent / "backups"
            if backup_dir.is_dir():
                for backup in backup_dir.glob(f"{path.stem}_*{path.suffix}"):
                    try:
                        backup.unlink()
                    except FileNotFoundError:
                        pass
        return removed

    def latest_for_source(self, source_path, *, include_completed=False) -> dict | None:
        self.cleanup_stale_previews()
        source_key = _path_key(source_path)
        candidates = []
        if not self.store_dir.is_dir():
            return None
        for path in self.store_dir.glob("*.json"):
            state = safe_read_json(path, None)
            if not isinstance(state, dict):
                continue
            if _path_key(state.get("source_path") or "") != source_key:
                continue
            allowed = {"failed", "running"}
            failure = state.get("failure")
            if isinstance(failure, Mapping) and bool(
                failure.get("retryable")
            ):
                allowed.add("blocked")
            if include_completed:
                allowed.add("completed")
            if state.get("status") not in allowed:
                continue
            try:
                modified_ns = int(path.stat().st_mtime_ns)
            except OSError:
                modified_ns = 0
            candidates.append((state, modified_ns))
        latest = max(
            candidates,
            key=lambda item: (
                str(item[0].get("updated_at") or ""),
                item[1],
                str(item[0].get("workflow_id") or ""),
            ),
            default=None,
        )
        return latest[0] if latest else None

    def latest_completed_for_source(self, source_path) -> dict | None:
        """Return the newest completed workflow for exactly one Excel source.

        A newer failed workflow must not shadow or silently replace the meaning
        of "방금 만든".  Conversely, an invalid newest completed state must
        fail closed rather than falling back to an older artifact.
        """
        self.cleanup_stale_previews()
        source_key = _path_key(source_path)
        candidates = []
        if not self.store_dir.is_dir():
            return None
        for path in self.store_dir.glob("*.json"):
            state = safe_read_json(path, None)
            if not isinstance(state, dict):
                continue
            if (
                _path_key(state.get("source_path") or "") == source_key
                and state.get("status") == "completed"
            ):
                try:
                    modified_ns = int(path.stat().st_mtime_ns)
                except OSError:
                    modified_ns = 0
                candidates.append((state, modified_ns))
        latest = max(
            candidates,
            key=lambda item: (
                str(item[0].get("updated_at") or ""),
                item[1],
                str(item[0].get("workflow_id") or ""),
            ),
            default=None,
        )
        return latest[0] if latest else None

    def latest_verified_artifact(
        self,
        source_path,
        artifact_kind: str,
    ) -> dict[str, Any]:
        """Resolve one unchanged artifact from the newest completed workflow."""
        state = self.latest_completed_for_source(source_path)
        if state is None:
            raise WorkflowError(
                "이 Excel 파일에서 완료된 최근 보고서·발표자료를 찾지 못했습니다."
            )
        if not _fingerprint_matches(
            state.get("source_path"), state.get("source_fingerprint")
        ):
            raise WorkflowError(
                "보고서·발표자료 생성 뒤 Excel 원본이 바뀌어 '방금 만든' 대상을 "
                "자동으로 선택하지 않았습니다. 원하는 파일을 직접 열어주세요."
            )
        self._state_step_order(state)
        state = self.load(str(state.get("workflow_id") or ""))
        report_format = _report_format(state.get("report_format") or "word")
        requested = str(artifact_kind or "").strip().casefold()
        if requested == "report":
            if report_format == "both":
                raise WorkflowError(
                    "최근 작업에 Word와 한글 보고서가 모두 있습니다. "
                    "'방금 만든 Word 보고서' 또는 '방금 만든 한글 보고서'라고 "
                    "지정해주세요."
                )
            requested = f"{report_format}_report"

        mapping = {
            "word_report": (
                "create_word_report",
                "word",
                "word",
                "Word 보고서",
            ),
            "hwp_report": (
                "create_hwp_report",
                "hwp",
                "hwp",
                "한글 보고서",
            ),
            "presentation": (
                "create_powerpoint_summary",
                "presentation",
                "powerpoint",
                "PowerPoint 발표자료",
            ),
        }
        if requested not in mapping:
            raise WorkflowError("열 최근 산출물 종류가 올바르지 않습니다.")
        step_name, output_kind, app_type, label = mapping[requested]
        if step_name not in self._state_step_order(state):
            raise WorkflowError(f"최근 완료 작업에는 {label}가 없습니다.")
        output_key = (
            "presentation"
            if requested == "presentation"
            else _report_output_key(report_format, output_kind)
        )
        step = dict((state.get("steps") or {}).get(step_name) or {})
        artifact = dict(step.get("artifact") or {})
        planned_path = str((state.get("output_paths") or {}).get(output_key) or "")
        if (
            step.get("status") != "succeeded"
            or not self._artifact_valid(artifact)
            or _path_key(artifact.get("path") or "") != _path_key(planned_path)
        ):
            raise WorkflowError(
                f"최근 {label}가 이동·수정·삭제되어 자동으로 열지 않았습니다."
            )
        return {
            "workflow_id": str(state.get("workflow_id") or ""),
            "artifact_kind": requested,
            "label": label,
            "app_type": app_type,
            "path": str(artifact["path"]),
            "fingerprint": dict(artifact["fingerprint"]),
            "verified_at": str(step.get("completed_at") or ""),
        }

    @staticmethod
    def _artifact_valid(artifact) -> bool:
        value = dict(artifact or {})
        path = value.get("path")
        return bool(path) and _fingerprint_matches(path, value.get("fingerprint"))

    @staticmethod
    def _state_step_contracts(
        state: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        return _validated_state_step_contracts(state)

    @staticmethod
    def _state_step_order(state: Mapping[str, Any]) -> tuple[str, ...]:
        report_format = _report_format(state.get("report_format") or "word")
        if state.get("include_report") is False and report_format != "word":
            raise WorkflowError(
                "저장된 발표자료 전용 계획의 내부 보고서 형식이 올바르지 않습니다."
            )
        expected = _step_order(
            report_format,
            state.get("include_presentation"),
            state.get("include_report"),
        )
        actual = tuple(state.get("step_order") or ())
        if actual != expected or set(state.get("steps") or {}) != set(expected):
            raise WorkflowError("저장된 워크플로 단계 구성이 올바르지 않습니다.")
        _validated_state_step_contracts(state)
        return expected

    def _reconcile(self, state: dict) -> None:
        if not _fingerprint_matches(state["source_path"], state["source_fingerprint"]):
            raise WorkflowError("준비 이후 Excel 원본 파일이 바뀌어 워크플로를 계속할 수 없습니다.")
        contracts = self._state_step_contracts(state)
        invalid_seen = False
        successful = []
        for name in self._state_step_order(state):
            step = state["steps"][name]
            dependencies_valid = all(
                dependency in successful
                for dependency in contracts[name]["depends_on"]
            )
            valid = step.get("status") == "succeeded" and dependencies_valid
            if name == "analyze_excel":
                if valid:
                    try:
                        WorkProductData.from_value(state.get("work_product") or {})
                    except WorkflowError:
                        valid = False
            elif valid:
                valid = self._artifact_valid(step.get("artifact"))
            if invalid_seen or not valid:
                if step.get("status") == "succeeded":
                    step["status"] = "pending"
                    step["error"] = "저장된 검증 결과가 더 이상 유효하지 않습니다."
                    step["artifact"] = None
                if not valid:
                    invalid_seen = True
            else:
                successful.append(name)
        state["successful_steps"] = successful

    def _step_context(self, state: dict, name: str) -> dict[str, Any]:
        context = {
            "workflow_id": state["workflow_id"],
            "source_path": state["source_path"],
            "title": state["title"],
            "output_dir": state["output_dir"],
            "preferences": dict(state.get("applied_preferences") or {}),
            "slide_count": int(state.get("slide_count") or 5),
            "report_format": _report_format(state.get("report_format") or "word"),
            "include_presentation": _include_presentation(
                state.get("include_presentation")
            ),
            "include_report": _include_report(state.get("include_report")),
            "join_plan": _validated_join_plan(state.get("join_plan")),
            "source_scope": _validated_source_scope(
                state.get("source_scope")
            ),
        }
        if name == "create_word_report":
            context["output_path"] = state["output_paths"][
                _report_output_key(context["report_format"], "word")
            ]
        elif name == "create_hwp_report":
            context["output_path"] = state["output_paths"][
                _report_output_key(context["report_format"], "hwp")
            ]
        elif name == "create_powerpoint_summary":
            context["output_path"] = state["output_paths"]["presentation"]
        return context

    def _run_step(self, state: dict, name: str):
        if name not in EXECUTABLE_STEP_NAMES:
            raise WorkflowError("등록된 실행기가 없는 워크플로 단계입니다.")
        context = self._step_context(state, name)
        if name == "analyze_excel":
            return WorkProductData.from_value(self.analyzer.run(context)).to_dict()
        product = WorkProductData.from_value(state.get("work_product") or {}).to_dict()
        if name == "create_word_report":
            return dict(self.word_writer.run(context, product))
        if name == "create_hwp_report":
            return dict(self.hwp_writer.run(context, product))
        if name == "create_powerpoint_summary":
            return dict(self.powerpoint_writer.run(context, product))
        raise WorkflowError("등록된 실행기가 없는 워크플로 단계입니다.")

    def run(self, workflow_id: str) -> dict[str, Any]:
        dependencies = {
            "error": WorkflowError,
            "join_error": WorkflowJoinValidationError,
            "scope_error": WorkflowSourceScopeValidationError,
            "execution_error": WorkflowExecutionError,
            "validate_scope": _validated_source_scope,
            "timestamp": _timestamp,
        }
        return WorkflowStepRunService(self, dependencies).run(workflow_id)

    @staticmethod
    def _result(state: dict, *, changed: bool) -> dict[str, Any]:
        step_contracts = _validated_state_step_contracts(state)
        step_recipe = report_workflow_step_recipe(
            _report_format(state.get("report_format") or "word"),
            _include_presentation(state.get("include_presentation")),
            _include_report(state.get("include_report")),
        )
        return {
            "success": True,
            "verified": state.get("status") == "completed",
            "changed": bool(changed),
            "workflow_id": state["workflow_id"],
            "status": state["status"],
            "source_path": state["source_path"],
            "created_files": list(state.get("created_files") or []),
            "successful_steps": list(state.get("successful_steps") or []),
            "step_contracts_verified": True,
            "step_contract_count": len(step_contracts),
            "step_registry_schema_version": (
                WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION
            ),
            "registered_step_recipe_verified": (
                list(step_contracts) == [item["step_name"] for item in step_recipe]
                and all(
                    step_contracts[item["step_name"]][field] == item[field]
                    for item in step_recipe
                    for field in (
                        "depends_on",
                        "effect",
                        "completion_evidence",
                    )
                )
            ),
            "verification_results": dict(state.get("verification_results") or {}),
            "output_paths": dict(state.get("output_paths") or {}),
            "slide_count": int(state.get("slide_count") or 5),
            "report_format": _report_format(state.get("report_format") or "word"),
            "include_presentation": _include_presentation(
                state.get("include_presentation")
            ),
            "include_report": _include_report(state.get("include_report")),
            "join_plan": _validated_join_plan(state.get("join_plan")),
            "source_scope": _validated_source_scope(
                state.get("source_scope")
            ),
            "report_formats": (
                list(_report_kinds(state.get("report_format") or "word"))
                if _include_report(state.get("include_report"))
                else []
            ),
            "applied_preferences": dict(state.get("applied_preferences") or {}),
            "work_product_summary": {
                "title": str((state.get("work_product") or {}).get("title") or ""),
                "metric_count": len((state.get("work_product") or {}).get("metrics") or []),
                "table_count": len((state.get("work_product") or {}).get("tables") or []),
                "chart_count": len((state.get("work_product") or {}).get("charts") or []),
                "insight_count": len((state.get("work_product") or {}).get("insights") or []),
            },
        }
