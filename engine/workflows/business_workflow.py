"""Stage 10: resumable Excel-to-Word-and-PowerPoint business workflows."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from engine.learning import validate_preference_value
from engine.runtime_paths import USER_DATA_DIR
from engine.storage.json_store import atomic_write_json, safe_read_json


WORKFLOW_SCHEMA_VERSION = 2
STEP_NAMES = (
    "analyze_excel",
    "create_word_report",
    "create_powerpoint_summary",
)
MAX_WORKSHEETS = 20
MAX_SOURCE_CELLS_PER_SHEET = 50_000
MAX_SOURCE_CELLS = 100_000
MAX_TABLE_ROWS = 100
MAX_TOTAL_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 30
MAX_METRICS = 50
MAX_INSIGHTS = 50
MAX_CHARTS = 20
MAX_WORK_PRODUCT_BYTES = 1_000_000
SUPPORTED_EXCEL_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls"})
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

    def __init__(self, workflow_id: str, step: str, message: str):
        self.workflow_id = str(workflow_id)
        self.step = str(step)
        self.failed_step = self.step
        self.diagnostic_context = {
            "workflow_hash": hashlib.sha256(
                self.workflow_id.encode("utf-8")
            ).hexdigest().upper(),
            "failed_step": self.failed_step,
        }
        super().__init__(
            f"워크플로 {self.workflow_id}의 {self.step} 단계에서 실패했습니다: {message} "
            "같은 Excel 문서에서 '실패한 워크플로 이어서'라고 요청하면 이 단계부터 다시 시도합니다."
        )


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

    def run(self, context: Mapping[str, Any]) -> dict[str, Any]:
        from engine.app_actions.com_lifecycle import com_apartment

        source_path = _absolute_path(context["source_path"])
        lease = None
        workbook = None
        with com_apartment(self._com_runtime):
            try:
                lease, workbook = self._open(source_path)
                if _path_key(self._workbook_path(workbook)) != _path_key(source_path):
                    raise WorkflowError("연결된 Excel 원본과 다른 통합문서는 분석하지 않습니다.")
                worksheets = workbook.Worksheets
                visible_sheets = []
                for index in range(1, int(worksheets.Count) + 1):
                    worksheet = worksheets.Item(index)
                    try:
                        visible = int(getattr(worksheet, "Visible", -1)) == -1
                    except Exception:
                        visible = True
                    if visible:
                        visible_sheets.append(worksheet)
                if not visible_sheets:
                    raise WorkflowError("표시된 Excel 시트가 없어 분석할 수 없습니다.")
                if len(visible_sheets) > MAX_WORKSHEETS:
                    raise WorkflowError(
                        f"한 번에 분석할 수 있는 표시 시트는 {MAX_WORKSHEETS}개까지입니다."
                    )

                sheet_ranges = []
                total_source_cells = 0
                for worksheet in visible_sheets:
                    sheet_name = str(getattr(worksheet, "Name", "") or "Sheet")
                    used = worksheet.UsedRange
                    rows = int(used.Rows.Count)
                    columns = int(used.Columns.Count)
                    if rows < 1 or columns < 1:
                        continue
                    sheet_cells = rows * columns
                    if sheet_cells > MAX_SOURCE_CELLS_PER_SHEET:
                        raise WorkflowError(
                            f"'{sheet_name}' 시트는 {MAX_SOURCE_CELLS_PER_SHEET:,}셀을 넘어 "
                            "한 번에 분석할 수 없습니다."
                        )
                    if columns > MAX_TABLE_COLUMNS:
                        raise WorkflowError(
                            f"'{sheet_name}' 시트는 {MAX_TABLE_COLUMNS}열을 넘어 "
                            "한 번에 분석할 수 없습니다."
                        )
                    total_source_cells += sheet_cells
                    if total_source_cells > MAX_SOURCE_CELLS:
                        raise WorkflowError(
                            f"표시 시트 전체 분석 범위는 {MAX_SOURCE_CELLS:,}셀까지입니다."
                        )
                    sheet_ranges.append(
                        (worksheet, used, sheet_name, rows, columns, sheet_cells)
                    )

                metrics = []
                tables = []
                charts = []
                remaining_table_rows = MAX_TOTAL_TABLE_ROWS
                qualify_metrics = len(visible_sheets) > 1
                for (
                    worksheet,
                    used,
                    sheet_name,
                    rows,
                    columns,
                    sheet_cells,
                ) in sheet_ranges:
                    matrix = self._matrix(used.Value2, rows, columns)
                    if not matrix or not any(
                        item not in (None, "") for row in matrix for item in row
                    ):
                        continue
                    raw_headers = matrix[0]
                    headers = [
                        str(value).strip() if value not in (None, "") else f"열 {index}"
                        for index, value in enumerate(raw_headers, 1)
                    ]
                    data_rows = matrix[1:]
                    for column, header in enumerate(headers):
                        if len(metrics) >= MAX_METRICS:
                            break
                        numbers = [
                            number
                            for row in data_rows
                            for number in [self._number(
                                row[column] if column < len(row) else None
                            )]
                            if number is not None
                        ]
                        if not numbers:
                            continue
                        metric_name = f"{sheet_name}/{header}" if qualify_metrics else header
                        metrics.append({
                            "name": metric_name,
                            "sheet_name": sheet_name,
                            "column_name": header,
                            "count": len(numbers),
                            "sum": round(sum(numbers), 6),
                            "average": round(sum(numbers) / len(numbers), 6),
                            "minimum": min(numbers),
                            "maximum": max(numbers),
                        })
                    included_count = min(
                        len(data_rows), MAX_TABLE_ROWS, remaining_table_rows
                    )
                    table_rows = [
                        (row + [None] * columns)[:columns]
                        for row in data_rows[:included_count]
                    ]
                    remaining_table_rows -= included_count
                    tables.append({
                        "name": sheet_name,
                        "headers": headers,
                        "rows": table_rows,
                        "total_rows": max(0, rows - 1),
                        "included_rows": len(table_rows),
                        "used_cells": sheet_cells,
                    })
                    charts.extend(self._chart_data(
                        worksheet, MAX_CHARTS - len(charts)
                    ))

                if not tables:
                    raise WorkflowError("표시된 Excel 시트에 분석할 데이터가 없습니다.")
                summary_lines = max(1, min(int(context.get("preferences", {}).get("summary_lines", 8)), 20))
                insights = [
                    f"{item['name']} 합계는 {item['sum']:,}, 평균은 {item['average']:,}입니다."
                    for item in metrics[:summary_lines]
                ]
                if not insights:
                    insights.append("숫자형 열이 없어 표 구조와 원본 행 수를 중심으로 정리했습니다.")
                product = WorkProductData(
                    title=str(context.get("title") or f"{Path(source_path).stem} 분석"),
                    metrics=metrics,
                    tables=tables,
                    charts=charts,
                    insights=insights,
                    source_files=[source_path],
                )
                return product.to_dict()
            finally:
                used = None
                worksheet = None
                worksheets = None
                sheet_ranges = []
                visible_sheets = []
                workbook = None
                if lease is not None:
                    lease.cleanup()


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
    def _slide_content(
        product: WorkProductData,
        preferences=None,
        slide_count=5,
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
        ) + "\r\n• 상세 수치와 표는 함께 생성된 Word 보고서를 확인하세요."
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
                for index, (title, body) in enumerate(
                    self._slide_content(
                        product,
                        context.get("preferences"),
                        expected_slides,
                    ),
                    1,
                ):
                    layout = 1 if index == 1 else 2  # ppLayoutTitle / ppLayoutText
                    slide = presentation.Slides.Add(index, layout)
                    slide.Shapes.Title.TextFrame.TextRange.Text = str(title)[:500]
                    slide.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = str(body)[:5_000]
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
        powerpoint_writer=None,
    ):
        self.store_dir = Path(store_dir or (Path(USER_DATA_DIR) / "workflows"))
        self.analyzer = analyzer or ExcelSalesAnalyzer()
        self.word_writer = word_writer or WordReportWriter()
        self.powerpoint_writer = powerpoint_writer or PowerPointSummaryWriter()

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
        schema_version = int(state.get("schema_version") or 0)
        if schema_version not in {1, WORKFLOW_SCHEMA_VERSION}:
            raise WorkflowError("지원하지 않는 워크플로 저장 형식입니다.")
        if schema_version == 1:
            state = copy.deepcopy(state)
            state["schema_version"] = WORKFLOW_SCHEMA_VERSION
            state["migrated_from_schema"] = 1
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
    ) -> dict:
        source = Path(_absolute_path(source_path))
        if not source.is_file() or source.suffix.casefold() not in SUPPORTED_EXCEL_SUFFIXES:
            raise WorkflowError("저장된 Excel 파일(.xlsx/.xlsm/.xlsb/.xls)이 필요합니다.")
        learned = _validated_workflow_preferences(preferences)
        try:
            slide_count = int(slide_count)
        except (TypeError, ValueError) as error:
            raise WorkflowError("PPT 장수는 숫자여야 합니다.") from error
        if not 3 <= slide_count <= 20:
            raise WorkflowError("PPT 장수는 3~20장 범위여야 합니다.")
        destination = Path(_absolute_path(output_dir or source.parent))
        if not destination.is_dir():
            raise WorkflowError("산출물 폴더를 찾을 수 없습니다.")
        workflow_id = uuid.uuid4().hex
        token = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + workflow_id
        source_stem = _safe_stem(source.stem)
        report_path = _reserve_output(destination, f"{source_stem}_JARVIS_보고서", ".docx", token)
        presentation_path = _reserve_output(
            destination,
            f"{source_stem}_JARVIS_{slide_count}장_요약",
            ".pptx",
            token,
        )
        now = _timestamp()
        if title is None:
            style = str(learned.get("title_style") or "default")
            title = {
                "short": source.stem,
                "noun": f"{source.stem} 분석 보고서",
                "sentence": f"{source.stem} 분석 결과를 보고합니다",
            }.get(style, f"{source.stem} 분석")
        state = {
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "status": "approval_required",
            "title": str(title),
            "source_path": str(source),
            "source_fingerprint": file_fingerprint(source),
            "output_dir": str(destination),
            "output_paths": {
                "report": str(report_path),
                "presentation": str(presentation_path),
            },
            "slide_count": slide_count,
            "explicit_slide_count": bool(explicit_slide_count),
            "applied_preferences": learned,
            "current_step": None,
            "successful_steps": [],
            "failed_step": None,
            "created_files": [],
            "verification_results": {},
            "work_product": None,
            "steps": {
                name: {
                    "status": "pending",
                    "attempts": 0,
                    "error": None,
                    "artifact": None,
                    "started_at": None,
                    "completed_at": None,
                }
                for name in STEP_NAMES
            },
            "created_at": now,
            "updated_at": now,
        }
        # Preview plans remain in the in-memory confirmation payload.  The
        # first durable workflow state is written only after explicit approval.
        return copy.deepcopy(state)

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
        if set(state.get("steps") or {}) != set(STEP_NAMES):
            raise WorkflowError("워크플로 단계 구성이 올바르지 않습니다.")
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

        outputs = dict(state.get("output_paths") or {})
        expected = {"report": ".docx", "presentation": ".pptx"}
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
            if include_completed:
                allowed.add("completed")
            if state.get("status") not in allowed:
                continue
            candidates.append(state)
        return max(candidates, key=lambda item: str(item.get("updated_at") or ""), default=None)

    @staticmethod
    def _artifact_valid(artifact) -> bool:
        value = dict(artifact or {})
        path = value.get("path")
        return bool(path) and _fingerprint_matches(path, value.get("fingerprint"))

    def _reconcile(self, state: dict) -> None:
        if not _fingerprint_matches(state["source_path"], state["source_fingerprint"]):
            raise WorkflowError("준비 이후 Excel 원본 파일이 바뀌어 워크플로를 계속할 수 없습니다.")
        invalid_seen = False
        successful = []
        for name in STEP_NAMES:
            step = state["steps"][name]
            valid = step.get("status") == "succeeded"
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
        }
        if name == "create_word_report":
            context["output_path"] = state["output_paths"]["report"]
        elif name == "create_powerpoint_summary":
            context["output_path"] = state["output_paths"]["presentation"]
        return context

    def _run_step(self, state: dict, name: str):
        context = self._step_context(state, name)
        if name == "analyze_excel":
            return WorkProductData.from_value(self.analyzer.run(context)).to_dict()
        product = WorkProductData.from_value(state.get("work_product") or {}).to_dict()
        if name == "create_word_report":
            return dict(self.word_writer.run(context, product))
        return dict(self.powerpoint_writer.run(context, product))

    def run(self, workflow_id: str) -> dict[str, Any]:
        state = self.load(workflow_id)
        if state.get("status") in {"approval_required", "cancelled"}:
            raise WorkflowError("승인되지 않은 워크플로 상태는 실행할 수 없습니다.")
        if state.get("status") == "completed":
            self._reconcile(state)
            if len(state["successful_steps"]) == len(STEP_NAMES):
                return self._result(state, changed=False)
        self._reconcile(state)
        state["status"] = "running"
        state["failed_step"] = None
        self._save(state)
        for name in STEP_NAMES:
            step = state["steps"][name]
            if name in state["successful_steps"]:
                continue
            state["current_step"] = name
            step["status"] = "running"
            step["attempts"] = int(step.get("attempts") or 0) + 1
            step["started_at"] = _timestamp()
            step["error"] = None
            self._save(state)
            try:
                artifact = self._run_step(state, name)
                if name == "analyze_excel":
                    state["work_product"] = artifact
                    verification = {
                        "valid_common_model": True,
                        "metric_count": len(artifact["metrics"]),
                        "table_count": len(artifact["tables"]),
                        "sheet_count": len(artifact["tables"]),
                        "chart_count": len(artifact["charts"]),
                    }
                    stored_artifact = None
                else:
                    stored_artifact = dict(artifact)
                    if not self._artifact_valid(stored_artifact):
                        raise WorkflowError("생성된 산출물의 파일 지문 검증에 실패했습니다.")
                    verification = dict(stored_artifact.get("verification") or {})
                    created_path = str(stored_artifact["path"])
                    if created_path not in state["created_files"]:
                        state["created_files"].append(created_path)
                step["artifact"] = stored_artifact
                step["status"] = "succeeded"
                step["completed_at"] = _timestamp()
                state["verification_results"][name] = verification
                if name not in state["successful_steps"]:
                    state["successful_steps"].append(name)
                self._save(state)
            except Exception as error:
                step["status"] = "failed"
                step["error"] = str(error)[:2_000]
                state["status"] = "failed"
                state["failed_step"] = name
                state["current_step"] = name
                self._save(state)
                raise WorkflowExecutionError(state["workflow_id"], name, str(error)) from error
        state["status"] = "completed"
        state["current_step"] = None
        state["failed_step"] = None
        self._save(state)
        return self._result(state, changed=True)

    @staticmethod
    def _result(state: dict, *, changed: bool) -> dict[str, Any]:
        return {
            "success": True,
            "verified": state.get("status") == "completed",
            "changed": bool(changed),
            "workflow_id": state["workflow_id"],
            "status": state["status"],
            "source_path": state["source_path"],
            "created_files": list(state.get("created_files") or []),
            "successful_steps": list(state.get("successful_steps") or []),
            "verification_results": dict(state.get("verification_results") or {}),
            "output_paths": dict(state.get("output_paths") or {}),
            "slide_count": int(state.get("slide_count") or 5),
            "applied_preferences": dict(state.get("applied_preferences") or {}),
            "work_product_summary": {
                "title": str((state.get("work_product") or {}).get("title") or ""),
                "metric_count": len((state.get("work_product") or {}).get("metrics") or []),
                "table_count": len((state.get("work_product") or {}).get("tables") or []),
                "chart_count": len((state.get("work_product") or {}).get("charts") or []),
                "insight_count": len((state.get("work_product") or {}).get("insights") or []),
            },
        }
