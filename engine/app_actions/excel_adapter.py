"""Safe native Excel actions with prepare/execute/verify boundaries."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from contextlib import contextmanager

import psutil

from engine.app_actions.base import (
    AppActionAmbiguousTarget,
    AppActionBlocked,
    AppActionBusy,
    AppActionContextChanged,
    AppActionError,
    AppActionUnavailable,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.com_lifecycle import (
    OfficeApplicationLease,
    application_lease,
    com_apartment,
)
from engine.app_actions.office_helpers import (
    excel_format_state_matches,
    excel_range_bounds,
    prepared_at_timestamp,
    replace_excel_text,
    stable_state_fingerprint,
)
from engine.app_actions.operations.excel import (
    EXCEL_OPERATIONS,
    EXCEL_UNDO_OPERATIONS,
    condition_true,
    normalize_color,
    normalize_operator,
    normalize_threshold,
    table_values,
)
from engine.app_actions.value_normalizer import (
    excel_column_letters,
    excel_column_number,
    excel_formulas_equal,
    excel_values_equal,
    normalize_range_address,
    normalize_single_column_range,
    serializable_excel_value,
)
from engine.vocabulary.alignment import normalize_alignment

XL_WORKSHEET = -4167
XL_UP = -4162
XL_CELL_VALUE = 1
XL_NONE = -4142
XL_ASCENDING = 1
XL_DESCENDING = 2
XL_YES = 1
XL_NO = 2
XL_SORT_COLUMNS = 1
MAX_SOURCE_CELLS = 10000
MAX_DIRECT_FORMAT_CELLS = 1000
MAX_RANGE_FORMAT_CELLS = 5000
MAX_FIND_REPLACE_CELLS = 10000
MAX_SORT_ROWS = 5000
MAX_TABLE_CELLS = 20000


ALIGNMENT_VALUES = {
    "left": -4131,
    "center": -4108,
    "right": -4152,
}


def _default_application_getter():
    import win32com.client

    return win32com.client.GetActiveObject("Excel.Application")


def create_owned_excel_application(application_factory=None):
    """Create a dedicated Excel instance whose lifetime belongs to Jarvis."""
    if application_factory is None:
        import win32com.client

        application_factory = win32com.client.DispatchEx
    application = application_factory("Excel.Application")
    return OfficeApplicationLease(
        application=application,
        owns_application=True,
        application_kind="excel",
    )


def _default_process_counter():
    count = 0
    for process in psutil.process_iter(["name"]):
        try:
            if str(process.info.get("name") or "").casefold() == "excel.exe":
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return count


class ExcelAdapter:
    """COM lifecycle, workbook/sheet context and the shared result shape.

    Every user-visible action lives in ``engine.app_actions.operations.excel``.
    The readers, verifiers and restore helpers below stay here because more
    than one operation uses them.
    """

    supported_operations = EXCEL_OPERATIONS.names
    undo_supported_operations = EXCEL_UNDO_OPERATIONS

    def __init__(
        self,
        application_getter=None,
        process_counter=None,
        discovery_attempts=6,
        discovery_retry_delay=0.15,
        com_runtime=None,
    ):
        self._uses_default_application_getter = application_getter is None
        self._application_getter = application_getter or _default_application_getter
        self._process_counter = process_counter or _default_process_counter
        self._com_runtime = com_runtime
        self._discovery_attempts = max(1, min(int(discovery_attempts), 10))
        self._discovery_retry_delay = max(
            0.0, min(float(discovery_retry_delay), 0.5)
        )

    def _targeted_clone_kwargs(self) -> dict:
        return {}

    def for_edit_session(self, session):
        """Return an adapter bound to one saved or unsaved Excel workbook."""
        if not self._uses_default_application_getter:
            return self
        target = dict(session or {})

        def application_getter():
            from engine.edit_mode.native_bridge import (
                excel_reference_for_identity,
            )

            application, _, _ = excel_reference_for_identity(
                expected_path=str(target.get("file_path") or ""),
                window_handle=int(target.get("window_handle") or 0),
                runtime_document_id=str(
                    target.get("runtime_document_id") or ""
                ),
                document_name=str(target.get("document_name") or ""),
            )
            if application is None:
                raise AppActionUnavailable(
                    "연결한 Excel 통합문서를 해당 창에서 다시 찾지 못했습니다."
                )
            return application

        return self.__class__(
            application_getter=application_getter,
            process_counter=lambda: 1,
            discovery_attempts=self._discovery_attempts,
            discovery_retry_delay=self._discovery_retry_delay,
            com_runtime=self._com_runtime,
            **self._targeted_clone_kwargs(),
        )

    @contextmanager
    def _application(self):
        with com_apartment(self._com_runtime):
            with self._application_reference() as application:
                yield application

    @contextmanager
    def _application_reference(self):
        lease = None
        try:
            application = self._retry_discovery(
                self._application_getter,
                "실행 중인 Excel을 찾지 못했습니다. Excel에서 통합문서를 먼저 열어주세요.",
            )
            lease = application_lease(application, "excel")
            application = lease.application
        except AppActionBusy:
            raise
        except Exception as error:
            raise AppActionUnavailable(
                "실행 중인 Excel을 찾지 못했습니다. Excel에서 통합문서를 먼저 열어주세요."
            ) from error
        if application is None:
            raise AppActionUnavailable(
                "실행 중인 Excel을 찾지 못했습니다. Excel에서 통합문서를 먼저 열어주세요."
            )
        try:
            yield application
        finally:
            lease.cleanup()
            application = None

    @staticmethod
    def _document_id(workbook) -> str:
        path = str(getattr(workbook, "Path", "") or "").strip()
        name = str(getattr(workbook, "Name", "") or "").strip()
        if path:
            full_name = str(
                getattr(workbook, "FullName", "") or os.path.join(path, name)
            )
            return os.path.normcase(os.path.abspath(full_name))
        return f"unsaved:{name}"

    @staticmethod
    def _com_error_code(error):
        current = error
        visited = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            args = getattr(current, "args", ())
            if args and isinstance(args[0], int):
                return int(args[0])
            current = getattr(current, "__cause__", None)
        return None

    def _retry_discovery(self, callback, generic_message):
        last_error = None
        for attempt in range(self._discovery_attempts):
            try:
                return callback()
            except AppActionBlocked:
                raise
            except Exception as error:
                last_error = error
                if attempt + 1 >= self._discovery_attempts:
                    break
                try:
                    import pythoncom

                    pythoncom.PumpWaitingMessages()
                except Exception:
                    pass
                time.sleep(self._discovery_retry_delay)

        error_code = self._com_error_code(last_error)
        if error_code in {-2147418111, -2147417846} or isinstance(
            last_error, AttributeError
        ):
            raise AppActionBusy(
                "Excel이 셀 편집 중이거나 대화상자가 열려 있어 자동화 요청을 거부했습니다. "
                "Excel에서 Enter 또는 Esc로 편집을 끝내고 열린 대화상자를 닫은 뒤 다시 시도해주세요."
            ) from last_error
        raise AppActionUnavailable(generic_message) from last_error

    _state_fingerprint = staticmethod(stable_state_fingerprint)

    @staticmethod
    def _address(range_object) -> str:
        member = range_object.Address
        value = member(False, False) if callable(member) else member
        return str(value).replace("$", "").upper()

    @staticmethod
    def _cell_snapshot(cell) -> dict:
        has_formula = bool(getattr(cell, "HasFormula", False))
        return {
            "current_formula": str(cell.Formula) if has_formula else None,
            "current_value": serializable_excel_value(cell.Value2),
            "merged": bool(getattr(cell, "MergeCells", False)),
        }

    def _common_context(self, application):
        if int(self._process_counter()) > 1:
            raise AppActionBlocked(
                "Excel 인스턴스가 여러 개 실행 중이라 대상을 안전하게 정할 수 없습니다. 하나만 남긴 뒤 다시 시도해주세요."
            )

        workbook, sheet = self._retry_discovery(
            lambda: (application.ActiveWorkbook, application.ActiveSheet),
            "Excel의 활성 통합문서와 시트를 읽지 못했습니다.",
        )
        if workbook is None or sheet is None:
            raise AppActionUnavailable("Excel에서 활성 통합문서와 시트를 찾지 못했습니다.")
        if int(getattr(sheet, "Type", XL_WORKSHEET)) != XL_WORKSHEET:
            raise AppActionBlocked("현재 활성 시트는 일반 워크시트가 아닙니다.")
        if bool(getattr(workbook, "ReadOnly", False)):
            raise AppActionBlocked("현재 통합문서는 읽기 전용이라 변경하지 않았습니다.")
        if bool(getattr(sheet, "ProtectContents", False)):
            raise AppActionBlocked("현재 시트가 보호되어 있어 변경하지 않았습니다.")
        snapshot = {
            "application_hwnd": int(getattr(application, "Hwnd", 0) or 0),
            "document_id": self._document_id(workbook),
            "workbook_name": str(workbook.Name),
            "sheet": str(sheet.Name),
            "read_only": False,
            "protected": False,
        }
        return workbook, sheet, snapshot

    def _active_context(self, application, cell_address: str):
        workbook, sheet, base = self._common_context(application)

        def read_cell_context():
            cell = sheet.Range(cell_address)
            if int(getattr(cell, "CountLarge", getattr(cell, "Count", 1))) != 1:
                raise AppActionBlocked("한 번에 단일 셀만 입력할 수 있습니다.")
            cell_state = self._cell_snapshot(cell)
            if cell_state["merged"]:
                raise AppActionBlocked("병합된 셀에는 안전하게 입력할 수 없어 작업을 중단했습니다.")
            snapshot = {
                **base,
                "target": self._address(cell),
                **cell_state,
            }
            return workbook, sheet, cell, snapshot

        return self._retry_discovery(
            read_cell_context,
            f"Excel 셀 {cell_address}의 상태를 안전하게 확인하지 못했습니다.",
        )

    @staticmethod
    def _matches_desired(snapshot: dict, desired: dict) -> bool:
        if desired["kind"] == "formula":
            return excel_formulas_equal(
                snapshot.get("current_formula"), desired["value"]
            )
        return (
            snapshot.get("current_formula") is None
            and excel_values_equal(snapshot.get("current_value"), desired["value"])
        )

    _created_at = staticmethod(prepared_at_timestamp)

    @staticmethod
    def _is_empty(snapshot):
        return (
            snapshot.get("current_formula") is None
            and snapshot.get("current_value") in {None, ""}
        )

    def _resolve_source_range(self, sheet, params):
        explicit = params.get("source_range") or params.get("range")
        if explicit:
            normalized = normalize_single_column_range(explicit)
            source = sheet.Range(normalized)
            return source, normalized, None

        column_name = str(params.get("column_name") or "").strip()
        if not column_name:
            raise AppActionBlocked("합계나 서식을 적용할 열 이름 또는 범위가 필요합니다.")

        if re.fullmatch(r"[A-Za-z]{1,3}", column_name):
            column_number = excel_column_number(column_name)
            resolved_header = None
        else:
            used = sheet.UsedRange
            first_column = int(getattr(used, "Column", 1) or 1)
            column_count = int(getattr(used.Columns, "Count", 1) or 1)
            if column_count > 512:
                raise AppActionBlocked("열이 너무 많아 1행 머리글을 안전하게 검색하지 않았습니다.")
            matches = []
            wanted = column_name.casefold()
            for number in range(first_column, first_column + column_count):
                header = sheet.Cells(1, number).Value2
                if str(header if header is not None else "").strip().casefold() == wanted:
                    matches.append(number)
            if not matches:
                raise AppActionBlocked(
                    f"1행에서 '{column_name}' 열을 찾지 못했습니다. A열처럼 열 문자를 지정해도 됩니다."
                )
            if len(matches) > 1:
                labels = ", ".join(f"{excel_column_letters(item)}열" for item in matches)
                raise AppActionAmbiguousTarget(
                    f"1행에 '{column_name}' 머리글이 여러 개 있습니다({labels}). 사용할 열을 선택해주세요.",
                    candidates=[excel_column_letters(item) for item in matches],
                    target_name=column_name,
                )
            column_number = matches[0]
            resolved_header = column_name

        last_row = int(sheet.Cells(sheet.Rows.Count, column_number).End(XL_UP).Row)
        start_row = int(params.get("start_row", 2) or 2)
        if start_row < 1 or start_row > last_row:
            raise AppActionBlocked(f"{column_name} 열에 합계나 서식을 적용할 데이터가 없습니다.")
        column = excel_column_letters(column_number)
        normalized = f"{column}{start_row}:{column}{last_row}"
        source = sheet.Range(normalized)
        return source, normalized, resolved_header

    @staticmethod
    def _range_count(source):
        return int(getattr(source, "CountLarge", getattr(source, "Count", 1)))

    @staticmethod
    def _range_digest(source):
        payload = {
            "formula": serializable_excel_value(source.Formula),
            "value": serializable_excel_value(source.Value2),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest().upper()

    def _resolve_column_number(self, sheet, column_name):
        name = str(column_name or "").strip()
        if not name:
            raise AppActionBlocked("사용할 열 이름 또는 열 문자가 필요합니다.")
        if re.fullmatch(r"[A-Za-z]{1,3}", name):
            return excel_column_number(name), None
        used = sheet.UsedRange
        first_column = int(getattr(used, "Column", 1) or 1)
        column_count = int(getattr(used.Columns, "Count", 1) or 1)
        if column_count > 512:
            raise AppActionBlocked("열이 너무 많아 1행 머리글을 안전하게 검색하지 않았습니다.")
        matches = []
        wanted = name.casefold()
        for number in range(first_column, first_column + column_count):
            header = sheet.Cells(1, number).Value2
            if str(header if header is not None else "").strip().casefold() == wanted:
                matches.append(number)
        if not matches:
            raise AppActionBlocked(
                f"1행에서 '{name}' 열을 찾지 못했습니다. A열처럼 열 문자를 지정해도 됩니다."
            )
        if len(matches) > 1:
            labels = ", ".join(f"{excel_column_letters(item)}열" for item in matches)
            raise AppActionAmbiguousTarget(
                f"1행에 '{name}' 머리글이 여러 개 있습니다({labels}). 사용할 열을 선택해주세요.",
                candidates=[excel_column_letters(item) for item in matches],
                target_name=name,
            )
        return matches[0], name

    _range_bounds = staticmethod(excel_range_bounds)

    def _resolve_table_range(self, sheet, params, column_name=None):
        explicit = params.get("table_range")
        if explicit:
            table_address = normalize_range_address(explicit, allow_single_cell=False)
            first_column, first_row, last_column, last_row = self._range_bounds(
                table_address
            )
            if first_row != 1:
                raise AppActionBlocked("6단계 표 작업은 1행을 머리글로 사용하는 범위만 지원합니다.")
        else:
            used = sheet.UsedRange
            first_column = int(getattr(used, "Column", 1) or 1)
            column_count = int(getattr(used.Columns, "Count", 1) or 1)
            last_column = first_column + column_count - 1
            last_row = 1
            for number in range(first_column, last_column + 1):
                candidate = int(
                    sheet.Cells(sheet.Rows.Count, number).End(XL_UP).Row
                )
                last_row = max(last_row, candidate)
            table_address = (
                f"{excel_column_letters(first_column)}1:"
                f"{excel_column_letters(last_column)}{last_row}"
            )
        if last_row < 2:
            raise AppActionBlocked("머리글 아래에 표 데이터가 없습니다.")
        table = sheet.Range(table_address)
        count = self._range_count(table)
        if count > MAX_TABLE_CELLS:
            raise AppActionBlocked(
                f"표 작업은 한 번에 최대 {MAX_TABLE_CELLS:,}개 셀까지 지원합니다."
            )
        for number in range(first_column, last_column + 1):
            header = sheet.Cells(1, number).Value2
            if str(header if header is not None else "").strip() == "":
                raise AppActionBlocked(
                    f"{excel_column_letters(number)}1 머리글이 비어 있어 표 전체 범위를 안전하게 정하지 못했습니다."
                )
        field_index = None
        resolved_header = None
        column_number = None
        if column_name is not None:
            column_number, resolved_header = self._resolve_column_number(
                sheet, column_name
            )
            if not first_column <= column_number <= last_column:
                raise AppActionBlocked("선택한 열이 현재 표 범위 밖에 있습니다.")
            field_index = column_number - first_column + 1
        return {
            "range": table,
            "address": table_address,
            "first_column": first_column,
            "last_column": last_column,
            "last_row": last_row,
            "field_index": field_index,
            "column_number": column_number,
            "resolved_header": resolved_header,
            "count": count,
        }

    @staticmethod
    def _safe_property(owner, name, default=None):
        try:
            return serializable_excel_value(getattr(owner, name))
        except Exception:
            return default

    def _format_state(self, cell):
        return {
            "address": self._address(cell),
            "bold": self._safe_property(cell.Font, "Bold", False),
            "font_size": self._safe_property(cell.Font, "Size"),
            "font_color": self._safe_property(cell.Font, "Color"),
            "fill_color": self._safe_property(cell.Interior, "Color"),
            "fill_color_index": self._safe_property(
                cell.Interior, "ColorIndex", XL_NONE
            ),
            "alignment": self._safe_property(cell, "HorizontalAlignment"),
        }

    _format_state_matches = staticmethod(excel_format_state_matches)

    @staticmethod
    def _normalize_alignment(value):
        try:
            name = normalize_alignment(value, ALIGNMENT_VALUES)
        except ValueError as error:
            raise AppActionBlocked(str(error)) from error
        return name, ALIGNMENT_VALUES[name]


    @staticmethod
    def _range_is_blank(source):
        value = serializable_excel_value(source.Value2)

        def has_value(item):
            if isinstance(item, list):
                return any(has_value(child) for child in item)
            return item not in {None, ""}

        return not has_value(value)

    def _filter_state(self, sheet):
        state = {
            "auto_filter_mode": bool(getattr(sheet, "AutoFilterMode", False)),
            "filter_mode": bool(getattr(sheet, "FilterMode", False)),
            "range": None,
            "filters": [],
        }
        try:
            auto_filter = sheet.AutoFilter
        except Exception:
            auto_filter = None
        if auto_filter is None:
            return state
        try:
            state["range"] = self._address(auto_filter.Range)
        except Exception:
            state["range"] = None
        try:
            filters = auto_filter.Filters
            count = int(getattr(filters, "Count", 0) or 0)
        except Exception:
            return state
        for index in range(1, count + 1):
            descriptor = {"field": index, "on": False}
            try:
                item = filters.Item(index)
                descriptor["on"] = bool(getattr(item, "On", False))
                if descriptor["on"]:
                    descriptor["criteria1"] = self._safe_property(
                        item, "Criteria1"
                    )
                    descriptor["operator"] = self._safe_property(
                        item, "Operator"
                    )
                    descriptor["criteria2"] = self._safe_property(
                        item, "Criteria2"
                    )
            except Exception:
                descriptor["unreadable"] = True
            state["filters"].append(descriptor)
        return state

    @staticmethod
    def _criteria_equal(actual, expected):
        if excel_values_equal(actual, expected):
            return True
        return str(actual if actual is not None else "").strip().casefold() == str(
            expected if expected is not None else ""
        ).strip().casefold()

    @staticmethod
    def _filter_criteria(operator, value):
        text = str(value if value is not None else "")
        if operator == "eq":
            return "=" if text == "" else text
        symbols = {
            "ne": "<>",
            "ge": ">=",
            "gt": ">",
            "le": "<=",
            "lt": "<",
        }
        return symbols[operator] + text

    def _matching_filter(self, state, table_address, field_index, criteria):
        if str(state.get("range") or "").upper() != table_address.upper():
            return False
        for descriptor in state.get("filters", []):
            if (
                descriptor.get("field") == field_index
                and descriptor.get("on")
                and self._criteria_equal(descriptor.get("criteria1"), criteria)
            ):
                return True
        return False

    @staticmethod
    def _cell_in_range(cell_address, range_address):
        cell_match = re.fullmatch(r"([A-Z]{1,3})(\d+)", cell_address)
        range_match = re.fullmatch(
            r"([A-Z]{1,3})(\d+):([A-Z]{1,3})(\d+)", range_address
        )
        if not cell_match or not range_match:
            return False
        column, row = cell_match.group(1), int(cell_match.group(2))
        return (
            column == range_match.group(1) == range_match.group(3)
            and int(range_match.group(2)) <= row <= int(range_match.group(4))
        )

    @staticmethod
    def _flatten(value):
        if isinstance(value, (list, tuple)):
            for item in value:
                yield from ExcelAdapter._flatten(item)
        else:
            yield value

    @staticmethod
    def _fixed_aggregate(application, source, aggregation):
        worksheet_function = getattr(application, "WorksheetFunction", None)
        if worksheet_function is not None:
            try:
                function = (
                    worksheet_function.Average
                    if aggregation == "average" else worksheet_function.Sum
                )
                return serializable_excel_value(function(source))
            except Exception as error:
                raise AppActionBlocked(
                    "현재 값 집계를 계산하지 못했습니다. 숫자 데이터와 오류 셀을 확인해주세요."
                ) from error
        values = []
        for value in ExcelAdapter._flatten(source.Value2):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if math.isfinite(float(value)):
                    values.append(value)
        if aggregation == "average":
            if not values:
                raise AppActionBlocked("평균을 계산할 숫자 데이터가 없습니다.")
            return sum(values) / len(values)
        return sum(values)

    # The operations reach these through the adapter, so the shared surface
    # stays in one place even though each lives with its own operation.
    _normalize_threshold = staticmethod(normalize_threshold)
    _normalize_color = staticmethod(normalize_color)
    _normalize_operator = staticmethod(normalize_operator)
    _condition_true = staticmethod(condition_true)
    _table_values = staticmethod(table_values)
    _replace_text = staticmethod(replace_excel_text)

    def _condition_descriptors(self, source):
        """Read the range's existing FormatConditions without raising."""
        conditions = source.FormatConditions
        count = int(getattr(conditions, "Count", 0) or 0)
        descriptors = []
        for index in range(1, count + 1):
            try:
                condition = conditions.Item(index)
                descriptor = {"index": index}
                for key, attribute, converter, default in (
                    ("type", "Type", int, 0),
                    ("operator", "Operator", int, 0),
                    ("formula1", "Formula1", str, ""),
                    ("formula2", "Formula2", str, ""),
                ):
                    try:
                        descriptor[key] = converter(
                            getattr(condition, attribute, default) or default
                        )
                    except Exception:
                        descriptor[key] = default
                try:
                    descriptor["color"] = int(condition.Interior.Color or 0)
                except Exception:
                    descriptor["color"] = 0
                descriptors.append(descriptor)
            except Exception:
                descriptors.append({"unreadable": index})
        return descriptors

    def _range_cells(self, source):
        cells = source.Cells
        count = int(getattr(cells, "CountLarge", getattr(cells, "Count", 1)))
        for index in range(1, count + 1):
            item_member = getattr(cells, "Item", None)
            yield item_member(index) if callable(item_member) else cells(index)

    def prepare(self, operation: str, params: dict) -> PreparedAction:
        operation_module = EXCEL_OPERATIONS.require(operation)
        if not isinstance(params, dict):
            raise AppActionBlocked("Excel 작업의 params는 객체 형식이어야 합니다.")
        with self._application() as application:
            return operation_module.prepare(self, application, params)

    def context_identity(self) -> str:
        """Return a content-free identity for stale clarification checks."""
        with self._application() as application:
            _, _, base = self._common_context(application)
        return self._state_fingerprint({
            "application_hwnd": base["application_hwnd"],
            "document_id": base["document_id"],
            "sheet": base["sheet"],
        })

    @staticmethod
    def _ensure_same_context(current, prepared):
        if current.context_fingerprint != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인 이후 Excel 통합문서·시트·대상 또는 원본 데이터가 바뀌어 실행하지 않았습니다."
            )

    def execute(self, prepared: PreparedAction) -> dict:
        operation_module = EXCEL_OPERATIONS.require_prepared(prepared)
        with self._application() as application:
            return operation_module.execute(self, application, prepared)

    def _write_and_verify(self, cell, before, desired, prepared):
        original_formula = before.get("current_formula")
        original_value = before.get("current_value")
        try:
            if desired["kind"] == "formula":
                cell.Formula = desired["value"]
            else:
                cell.Value2 = desired["value"]
            after = self._verify_written_cell(cell, before, desired, prepared.target)
        except Exception as error:
            try:
                if original_formula is not None:
                    cell.Formula = original_formula
                else:
                    cell.Value2 = original_value
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                f"Excel {prepared.target} 입력 또는 검증에 실패했습니다."
            ) from error
        return self._result(prepared, before, after, changed=True)

    @staticmethod
    def _apply_desired_format(source, desired):
        if "bold" in desired:
            source.Font.Bold = desired["bold"]
        if "font_size" in desired:
            source.Font.Size = desired["font_size"]
        if "font_color" in desired:
            source.Font.Color = desired["font_color"]
        if "fill_color" in desired:
            source.Interior.Color = desired["fill_color"]
        if "alignment" in desired:
            source.HorizontalAlignment = desired["alignment"]

    @staticmethod
    def _restore_format(cell, original):
        cell.Font.Bold = original.get("bold")
        if original.get("font_size") is not None:
            cell.Font.Size = original.get("font_size")
        if original.get("font_color") is not None:
            cell.Font.Color = original.get("font_color")
        if original.get("fill_color_index") == XL_NONE:
            cell.Interior.ColorIndex = XL_NONE
        elif original.get("fill_color") is not None:
            cell.Interior.Color = original.get("fill_color")
        if original.get("alignment") is not None:
            cell.HorizontalAlignment = original.get("alignment")

    def _restore_filter_state(self, sheet, previous):
        if bool(getattr(sheet, "FilterMode", False)):
            sheet.ShowAllData()
        if bool(getattr(sheet, "AutoFilterMode", False)):
            sheet.AutoFilterMode = False
        address = previous.get("range")
        active = [
            item for item in previous.get("filters", []) if item.get("on")
        ]
        if not address or not previous.get("auto_filter_mode"):
            return
        source = sheet.Range(address)
        if not active:
            source.AutoFilter()
            return
        for descriptor in active:
            arguments = {
                "Field": descriptor["field"],
                "Criteria1": descriptor.get("criteria1"),
            }
            if descriptor.get("operator") not in {None, 0, "0"}:
                arguments["Operator"] = descriptor.get("operator")
            if descriptor.get("criteria2") is not None:
                arguments["Criteria2"] = descriptor.get("criteria2")
            source.AutoFilter(**arguments)

    def _verify_written_cell(self, cell, snapshot, desired, target):
        after = None
        last_error = None
        for attempt in range(self._discovery_attempts):
            try:
                after_formula = str(cell.Formula) if bool(cell.HasFormula) else None
                after = {
                    **snapshot,
                    "current_formula": after_formula,
                    "current_value": serializable_excel_value(cell.Value2),
                }
                last_error = None
                if self._matches_desired(after, desired):
                    return after
            except Exception as error:
                last_error = error
            if attempt + 1 < self._discovery_attempts:
                try:
                    import pythoncom

                    pythoncom.PumpWaitingMessages()
                except Exception:
                    pass
                time.sleep(self._discovery_retry_delay)

        if last_error is not None and after is None:
            raise AppActionVerificationError(
                f"Excel {target} 셀의 입력 결과를 다시 읽지 못했습니다."
            ) from last_error
        actual = (
            after.get("current_formula")
            if after and after.get("current_formula") is not None
            else after.get("current_value") if after else None
        )
        raise AppActionVerificationError(
            f"Excel {target} 셀을 다시 읽은 값이 요청과 다릅니다. "
            f"(요청: {str(desired.get('value'))[:120]}, 실제: {str(actual)[:120]})"
        )

    @staticmethod
    def _restore_cell_value(cell, state):
        formula = state.get("formula")
        if formula is not None:
            cell.Formula = formula
        else:
            cell.Value2 = state.get("value")

    def _ensure_undo_context(self, application, prepared):
        _, sheet, base = self._common_context(application)
        if (
            str(base["document_id"]).casefold()
            != str(prepared.document_id).casefold()
            or str(base["sheet"]).casefold() != str(prepared.sheet).casefold()
        ):
            raise AppActionContextChanged(
                "편집했던 Excel 통합문서와 시트가 현재 대상이 아니어서 복원하지 않았습니다."
            )
        return sheet

    def undo(self, prepared, record=None):
        """Restore one verified Excel edit from its structured snapshot."""
        if not isinstance(prepared, PreparedAction):
            prepared = PreparedAction.from_dict(prepared or {})
        if prepared.app != "excel" or not prepared.reversible:
            raise AppActionBlocked("복원할 수 있는 Excel 편집 작업이 아닙니다.")
        operation_module = EXCEL_OPERATIONS.get(prepared.operation)
        handler = getattr(operation_module, "undo", None) if operation_module else None
        if handler is None:
            raise AppActionBlocked("이 Excel 작업은 자동 복원을 지원하지 않습니다.")
        try:
            with self._application() as application:
                restored = handler(self, application, prepared)
        except AppActionError:
            raise
        except Exception as error:
            raise AppActionVerificationError(
                "Excel 원상 복원 또는 복원 검증에 실패했습니다."
            ) from error
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "excel",
            "operation": "undo_last_edit",
            "document_id": prepared.document_id,
            "workbook_name": prepared.workbook_name,
            "sheet": prepared.sheet,
            "target": prepared.target,
            "changed": True,
            "verification_method": "structured_snapshot_restore_and_readback",
            "before": dict((record or {}).get("after_observations") or {}),
            "after": restored,
        }

    @staticmethod
    def _result(prepared, before, after, changed):
        return {
            "success": True,
            "verified": True,
            "status": "success",
            "app": "excel",
            "operation": prepared.operation,
            "document_id": prepared.document_id,
            "workbook_name": prepared.workbook_name,
            "sheet": prepared.sheet,
            "target": prepared.target,
            "changed": bool(changed),
            "verification_method": prepared.verification_method,
            "before": serializable_excel_value(before),
            "after": serializable_excel_value(after),
        }
