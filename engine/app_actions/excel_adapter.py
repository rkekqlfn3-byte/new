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
from engine.app_actions.value_normalizer import (
    excel_column_letters,
    excel_column_number,
    excel_formulas_equal,
    excel_values_equal,
    normalize_cell_address,
    normalize_excel_input,
    normalize_range_address,
    normalize_single_column_range,
    serializable_excel_value,
)


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

CONDITION_OPERATORS = {
    "eq": 3,
    "ne": 4,
    "gt": 5,
    "lt": 6,
    "ge": 7,
    "le": 8,
}

COLOR_VALUES = {
    "yellow": 65535,
    "red": 255,
    "green": 5287936,
    "blue": 12611584,
    "orange": 3243501,
    "gray": 12566463,
}

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
    supported_operations = frozenset({
        "write_cell",
        "sum_column_to_cell",
        "apply_conditional_format",
        "format_matching_values",
        "format_range",
        "filter_range",
        "find_replace",
        "sort_range",
        "insert_rows",
        "insert_columns",
    })

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

    def _prepare_write_in_app(self, application, params):
        cell_address = normalize_cell_address(params.get("cell"))
        desired = normalize_excel_input(params.get("value"), params.get("value_type"))
        _, _, _, snapshot = self._active_context(application, cell_address)
        is_empty = self._is_empty(snapshot)
        noop = self._matches_desired(snapshot, desired)
        return PreparedAction(
            app="excel",
            operation="write_cell",
            document_id=snapshot["document_id"],
            workbook_name=snapshot["workbook_name"],
            sheet=snapshot["sheet"],
            target=snapshot["target"],
            params={
                "cell": snapshot["target"],
                "value": desired["value"],
                "value_type": desired["kind"],
            },
            current_state={
                "formula": snapshot["current_formula"],
                "value": snapshot["current_value"],
                "empty": is_empty,
            },
            estimated_changes=0 if noop else 1,
            destructive=not is_empty and not noop,
            reversible=True,
            verification_method=(
                "read_formula" if desired["kind"] == "formula" else "read_value2"
            ),
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": snapshot["application_hwnd"]},
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
        alignment = str(value or "").strip().casefold()
        aliases = {
            "왼쪽": "left",
            "왼쪽 정렬": "left",
            "좌측": "left",
            "가운데": "center",
            "가운데 정렬": "center",
            "중앙": "center",
            "중앙 정렬": "center",
            "오른쪽": "right",
            "오른쪽 정렬": "right",
            "우측": "right",
        }
        alignment = aliases.get(alignment, alignment)
        if alignment not in ALIGNMENT_VALUES:
            raise AppActionBlocked("정렬은 왼쪽·가운데·오른쪽 중 하나여야 합니다.")
        return alignment, ALIGNMENT_VALUES[alignment]

    def _desired_range_format(self, params):
        supplied = params.get("desired")
        desired = {}
        labels = {}
        if isinstance(supplied, dict):
            for key in {
                "bold", "font_size", "font_color", "fill_color", "alignment"
            }:
                if key in supplied:
                    desired[key] = supplied[key]
            labels = dict(params.get("format_labels") or {})
        else:
            if params.get("bold") is not None:
                desired["bold"] = bool(params.get("bold"))
            if params.get("font_size") is not None:
                try:
                    size = float(params.get("font_size"))
                except (TypeError, ValueError) as error:
                    raise AppActionBlocked("글자 크기는 숫자로 지정해주세요.") from error
                if not 1 <= size <= 409:
                    raise AppActionBlocked("글자 크기는 1부터 409 사이여야 합니다.")
                desired["font_size"] = int(size) if size.is_integer() else size
            if params.get("alignment") is not None:
                label, value = self._normalize_alignment(params.get("alignment"))
                desired["alignment"] = value
                labels["alignment"] = label
            for parameter, key in (
                ("font_color", "font_color"),
                ("fill_color", "fill_color"),
            ):
                if params.get(parameter) is not None:
                    label, value = self._normalize_color(params.get(parameter))
                    desired[key] = value
                    labels[key] = label
        if not desired:
            raise AppActionBlocked("적용할 범위 서식을 하나 이상 지정해주세요.")
        allowed = {"bold", "font_size", "font_color", "fill_color", "alignment"}
        if set(desired) - allowed:
            raise AppActionBlocked("지원하지 않는 범위 서식이 포함되어 있습니다.")
        if "font_size" in desired:
            try:
                size = float(desired["font_size"])
            except (TypeError, ValueError) as error:
                raise AppActionBlocked("글자 크기는 숫자로 지정해주세요.") from error
            if not 1 <= size <= 409:
                raise AppActionBlocked("글자 크기는 1부터 409 사이여야 합니다.")
            desired["font_size"] = int(size) if size.is_integer() else size
        if "bold" in desired:
            desired["bold"] = bool(desired["bold"])
        for key in {"font_color", "fill_color", "alignment"} & set(desired):
            try:
                desired[key] = int(desired[key])
            except (TypeError, ValueError) as error:
                raise AppActionBlocked("범위 서식 값이 올바르지 않습니다.") from error
        return desired, labels

    def _prepare_range_format_in_app(self, application, params):
        _, sheet, base = self._common_context(application)
        address = normalize_range_address(params.get("range"))
        source = sheet.Range(address)
        count = self._range_count(source)
        if count > MAX_RANGE_FORMAT_CELLS:
            raise AppActionBlocked(
                f"범위 서식은 한 번에 최대 {MAX_RANGE_FORMAT_CELLS:,}개 셀까지 지원합니다."
            )
        desired, labels = self._desired_range_format(params)
        states = []
        changing = []
        for cell in self._range_cells(source):
            if bool(getattr(cell, "MergeCells", False)):
                raise AppActionBlocked("병합된 셀이 포함된 범위에는 안전하게 서식을 적용하지 않습니다.")
            state = self._format_state(cell)
            states.append(state)
            if not self._format_state_matches(state, desired):
                changing.append(state)
        snapshot = {
            **base,
            "operation": "format_range",
            "target": address,
            "source_digest": self._range_digest(source),
            "formats": states,
            "desired": desired,
        }
        noop = not changing
        return PreparedAction(
            app="excel",
            operation="format_range",
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=address,
            params={
                "range": address,
                "desired": desired,
                "format_labels": labels,
                "original_formats": changing,
            },
            current_state={
                "cell_count": count,
                "changing_count": len(changing),
            },
            estimated_changes=len(changing),
            destructive=len(changing) > 100,
            reversible=True,
            verification_method="read_cell_formats",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    @staticmethod
    def _range_is_blank(source):
        value = serializable_excel_value(source.Value2)

        def has_value(item):
            if isinstance(item, list):
                return any(has_value(child) for child in item)
            return item not in {None, ""}

        return not has_value(value)

    def _prepare_structure_insert_in_app(self, application, params, axis):
        _, sheet, base = self._common_context(application)
        selection = normalize_range_address(params.get("selection_range"))
        first_column, first_row, _, _ = self._range_bounds(selection)
        try:
            count = int(params.get("count", 1))
        except (TypeError, ValueError) as error:
            raise AppActionBlocked("추가할 행·열 개수는 숫자여야 합니다.") from error
        if not 1 <= count <= 10:
            raise AppActionBlocked("행·열 추가는 한 번에 1개부터 10개까지 지원합니다.")

        used = sheet.UsedRange
        used_address = self._address(used)
        used_first_column, used_first_row, used_last_column, used_last_row = (
            self._range_bounds(used_address)
        )
        used_count = self._range_count(used)
        if used_count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"행·열 추가는 사용 영역이 {MAX_SOURCE_CELLS:,}개 셀 이하인 시트에서만 지원합니다."
            )
        index = first_row if axis == "row" else first_column
        lower = used_first_row if axis == "row" else used_first_column
        upper = used_last_row if axis == "row" else used_last_column
        if not lower <= index <= upper:
            raise AppActionBlocked("데이터가 있는 사용 영역 안의 행 또는 열을 선택해주세요.")

        if axis == "row":
            shifted_address = (
                f"{excel_column_letters(used_first_column)}{index}:"
                f"{excel_column_letters(used_last_column)}{used_last_row}"
            )
            target = f"{index}행"
        else:
            shifted_address = (
                f"{excel_column_letters(index)}{used_first_row}:"
                f"{excel_column_letters(used_last_column)}{used_last_row}"
            )
            target = f"{excel_column_letters(index)}열"
        shifted_digest = self._range_digest(sheet.Range(shifted_address))
        operation = "insert_rows" if axis == "row" else "insert_columns"
        snapshot = {
            **base,
            "operation": operation,
            "selection": selection,
            "axis": axis,
            "index": index,
            "count": count,
            "used_address": used_address,
            "used_digest": self._range_digest(used),
            "shifted_address": shifted_address,
            "shifted_digest": shifted_digest,
        }
        affected = (
            (used_last_row - index + 1)
            * (used_last_column - used_first_column + 1)
            if axis == "row"
            else (used_last_column - index + 1)
            * (used_last_row - used_first_row + 1)
        )
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=target,
            params={
                "selection_range": selection,
                "axis": axis,
                "index": index,
                "count": count,
                "used_first_column": used_first_column,
                "used_first_row": used_first_row,
                "used_last_column": used_last_column,
                "used_last_row": used_last_row,
                "shifted_digest": shifted_digest,
            },
            current_state={
                "used_address": used_address,
                "affected_cells": affected,
            },
            estimated_changes=affected,
            destructive=True,
            reversible=True,
            verification_method=(
                "verify_shifted_rows"
                if axis == "row"
                else "verify_shifted_columns"
            ),
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            metadata={"application_hwnd": base["application_hwnd"]},
        )

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

    def _prepare_filter_in_app(self, application, params):
        _, sheet, base = self._common_context(application)
        previous = self._filter_state(sheet)
        clear = bool(params.get("clear"))
        if clear:
            target = previous.get("range")
            if not target:
                target = normalize_range_address(self._address(sheet.UsedRange))
            snapshot = {
                **base,
                "operation": "filter_range",
                "target": target,
                "filter_state": previous,
                "clear": True,
            }
            noop = not previous.get("filter_mode")
            return PreparedAction(
                app="excel",
                operation="filter_range",
                document_id=base["document_id"],
                workbook_name=base["workbook_name"],
                sheet=base["sheet"],
                target=target,
                params={"clear": True, "previous_filter": previous},
                current_state={
                    "filter_active": bool(previous.get("filter_mode")),
                    "active_filter_count": sum(
                        1 for item in previous.get("filters", []) if item.get("on")
                    ),
                },
                estimated_changes=0 if noop else 1,
                destructive=False,
                reversible=True,
                verification_method="read_autofilter_state",
                context_fingerprint=self._state_fingerprint(snapshot),
                prepared_at=self._created_at(),
                noop=noop,
                metadata={"application_hwnd": base["application_hwnd"]},
            )

        column_name = params.get("column_name")
        table = self._resolve_table_range(sheet, params, column_name)
        operator = self._normalize_operator(params.get("operator") or "eq")
        normalized = normalize_excel_input(params.get("value"))
        if normalized["kind"] == "formula":
            raise AppActionBlocked("필터 값에는 수식을 사용할 수 없습니다.")
        value = normalized["value"]
        criteria = self._filter_criteria(operator, value)
        noop = self._matching_filter(
            previous, table["address"], table["field_index"], criteria
        )
        active_count = sum(
            1 for item in previous.get("filters", []) if item.get("on")
        )
        snapshot = {
            **base,
            "operation": "filter_range",
            "target": table["address"],
            "table_digest": self._range_digest(table["range"]),
            "filter_state": previous,
            "field_index": table["field_index"],
            "criteria": criteria,
        }
        return PreparedAction(
            app="excel",
            operation="filter_range",
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=table["address"],
            params={
                "clear": False,
                "table_range": table["address"],
                "column_name": column_name,
                "field_index": table["field_index"],
                "operator": operator,
                "value": value,
                "criteria": criteria,
                "previous_filter": previous,
            },
            current_state={
                "row_count": table["last_row"] - 1,
                "active_filter_count": active_count,
                "filter_active": bool(previous.get("filter_mode")),
            },
            estimated_changes=0 if noop else table["last_row"] - 1,
            destructive=active_count > 0 and not noop,
            reversible=True,
            verification_method="read_autofilter_state",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    _replace_text = staticmethod(replace_excel_text)

    def _prepare_find_replace_in_app(self, application, params):
        _, sheet, base = self._common_context(application)
        scope = str(params.get("scope") or "range").strip().casefold()
        if scope not in {"range", "current_sheet"}:
            raise AppActionBlocked("찾기·바꾸기는 지정 범위 또는 현재 시트에서만 지원합니다.")
        if scope == "current_sheet":
            address = normalize_range_address(self._address(sheet.UsedRange))
        else:
            address = normalize_range_address(params.get("range"))
        source = sheet.Range(address)
        count = self._range_count(source)
        if count > MAX_FIND_REPLACE_CELLS:
            raise AppActionBlocked(
                f"찾기·바꾸기는 한 번에 최대 {MAX_FIND_REPLACE_CELLS:,}개 셀까지 지원합니다."
            )
        old = str(params.get("find") if params.get("find") is not None else "")
        new = str(
            params.get("replace") if params.get("replace") is not None else ""
        )
        if old == "":
            raise AppActionBlocked("찾을 문자열은 비워둘 수 없습니다.")
        whole_cell = bool(params.get("whole_cell"))
        match_case = bool(params.get("match_case"))
        matches = []
        for cell in self._range_cells(source):
            if bool(getattr(cell, "MergeCells", False)):
                raise AppActionBlocked("병합된 셀이 포함된 범위에서는 찾기·바꾸기를 실행하지 않습니다.")
            if bool(getattr(cell, "HasFormula", False)):
                continue
            value = cell.Value2
            if not isinstance(value, str):
                continue
            replacement = self._replace_text(
                value, old, new, whole_cell, match_case
            )
            if replacement is not None and replacement != value:
                matches.append({
                    "address": self._address(cell),
                    "original": value,
                    "replacement": replacement,
                })
        snapshot = {
            **base,
            "operation": "find_replace",
            "target": address,
            "scope": scope,
            "range_digest": self._range_digest(source),
            "find": old,
            "replace": new,
            "whole_cell": whole_cell,
            "match_case": match_case,
            "matches": matches,
        }
        noop = not matches
        return PreparedAction(
            app="excel",
            operation="find_replace",
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=address,
            params={
                "scope": scope,
                "range": address,
                "find": old,
                "replace": new,
                "whole_cell": whole_cell,
                "match_case": match_case,
                "matches": matches,
            },
            current_state={"cell_count": count, "matching_count": len(matches)},
            estimated_changes=len(matches),
            destructive=bool(matches),
            reversible=True,
            verification_method="read_replaced_cell_values",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    @staticmethod
    def _sort_key_kind(values):
        nonblank = [value for value in values if value not in {None, ""}]
        if not nonblank:
            raise AppActionBlocked("정렬 기준 열에 데이터가 없습니다.")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in nonblank
        ):
            return "number"
        if all(isinstance(value, str) for value in nonblank):
            return "text"
        raise AppActionBlocked("정렬 기준 열에 숫자와 문자가 섞여 있어 자동 정렬하지 않습니다.")

    @staticmethod
    def _sorted_keys(values, kind, descending):
        nonblank = [value for value in values if value not in {None, ""}]
        blanks = [value for value in values if value in {None, ""}]
        key = (lambda value: float(value)) if kind == "number" else (
            lambda value: value.casefold()
        )
        return sorted(nonblank, key=key, reverse=descending) + blanks

    def _table_values(self, sheet, table):
        values = []
        for row in range(2, table["last_row"] + 1):
            values.append([
                serializable_excel_value(sheet.Cells(row, column).Value2)
                for column in range(table["first_column"], table["last_column"] + 1)
            ])
        return values

    def _prepare_sort_in_app(self, application, params):
        _, sheet, base = self._common_context(application)
        if bool(getattr(sheet, "FilterMode", False)):
            raise AppActionBlocked("필터가 적용된 표는 필터를 해제한 뒤 정렬해주세요.")
        column_name = params.get("column_name")
        table = self._resolve_table_range(sheet, params, column_name)
        if table["last_row"] - 1 > MAX_SORT_ROWS:
            raise AppActionBlocked(
                f"정렬은 한 번에 최대 {MAX_SORT_ROWS:,}개 데이터 행까지 지원합니다."
            )
        if bool(getattr(table["range"], "MergeCells", False)):
            raise AppActionBlocked("병합된 셀이 포함된 표는 자동 정렬하지 않습니다.")
        direction = str(params.get("direction") or "ascending").strip().casefold()
        aliases = {
            "asc": "ascending", "오름차순": "ascending", "낮은순": "ascending",
            "desc": "descending", "내림차순": "descending", "높은순": "descending",
        }
        direction = aliases.get(direction, direction)
        if direction not in {"ascending", "descending"}:
            raise AppActionBlocked("정렬 방향은 오름차순 또는 내림차순이어야 합니다.")
        rows = self._table_values(sheet, table)
        original_cells = []
        for row_number in range(2, table["last_row"] + 1):
            row_snapshot = []
            for column_number in range(
                table["first_column"], table["last_column"] + 1
            ):
                cell = sheet.Cells(row_number, column_number)
                has_formula = bool(getattr(cell, "HasFormula", False))
                row_snapshot.append({
                    "formula": str(cell.Formula) if has_formula else None,
                    "value": serializable_excel_value(cell.Value2),
                })
            original_cells.append(row_snapshot)
        key_index = table["column_number"] - table["first_column"]
        keys = [row[key_index] for row in rows]
        kind = self._sort_key_kind(keys)
        expected_keys = self._sorted_keys(
            keys, kind, direction == "descending"
        )
        noop = keys == expected_keys
        snapshot = {
            **base,
            "operation": "sort_range",
            "target": table["address"],
            "table_digest": self._range_digest(table["range"]),
            "column_number": table["column_number"],
            "direction": direction,
            "rows": rows,
        }
        return PreparedAction(
            app="excel",
            operation="sort_range",
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=table["address"],
            params={
                "table_range": table["address"],
                "column_name": column_name,
                "column_number": table["column_number"],
                "direction": direction,
                "key_kind": kind,
                "expected_keys": expected_keys,
                "original_rows": rows,
                "original_cells": original_cells,
            },
            current_state={
                "row_count": len(rows),
                "column_count": table["last_column"] - table["first_column"] + 1,
            },
            estimated_changes=0 if noop else len(rows),
            destructive=not noop,
            reversible=True,
            verification_method="read_sorted_table",
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

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

    def _prepare_sum_in_app(self, application, params):
        _, sheet, base = self._common_context(application)
        source, source_address, header = self._resolve_source_range(sheet, params)
        count = self._range_count(source)
        if count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"한 번에 최대 {MAX_SOURCE_CELLS:,}개 셀까지 집계할 수 있습니다."
            )
        target_address = normalize_cell_address(
            params.get("target_cell") or params.get("cell")
        )
        if self._cell_in_range(target_address, source_address):
            raise AppActionBlocked("집계 결과 셀이 원본 범위 안에 있어 순환 참조가 생길 수 있습니다.")
        target = sheet.Range(target_address)
        if bool(getattr(target, "MergeCells", False)):
            raise AppActionBlocked("병합된 셀에는 집계 결과를 입력하지 않았습니다.")
        target_state = self._cell_snapshot(target)
        mode = str(
            params.get("result_mode") or params.get("write_mode") or "formula"
        ).strip().casefold()
        if mode not in {"formula", "value"}:
            raise AppActionBlocked("집계 결과 방식은 formula 또는 value여야 합니다.")
        aggregation = str(params.get("aggregation") or "sum").strip().casefold()
        if aggregation not in {"sum", "average"}:
            raise AppActionBlocked("지원하는 집계 방식은 합계와 평균입니다.")
        function_name = "AVERAGE" if aggregation == "average" else "SUM"
        desired = (
            {"kind": "formula", "value": f"={function_name}({source_address})"}
            if mode == "formula"
            else {
                "kind": "value",
                "value": self._fixed_aggregate(application, source, aggregation),
            }
        )
        snapshot = {
            **base,
            "operation": "sum_column_to_cell",
            "target": self._address(target),
            **target_state,
            "source_range": source_address,
            "source_digest": self._range_digest(source),
            "source_count": count,
            "result_mode": mode,
            "aggregation": aggregation,
        }
        is_empty = self._is_empty(snapshot)
        noop = self._matches_desired(snapshot, desired)
        return PreparedAction(
            app="excel",
            operation="sum_column_to_cell",
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=snapshot["target"],
            params={
                "source_range": source_address,
                "column_name": header,
                "target_cell": snapshot["target"],
                "result_mode": mode,
                "aggregation": aggregation,
                "value": desired["value"],
                "value_type": desired["kind"],
            },
            current_state={
                "formula": target_state["current_formula"],
                "value": target_state["current_value"],
                "empty": is_empty,
                "source_range": source_address,
                "source_count": count,
            },
            estimated_changes=0 if noop else 1,
            destructive=not is_empty and not noop,
            reversible=True,
            verification_method=(
                "read_formula" if desired["kind"] == "formula" else "read_value2"
            ),
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    @staticmethod
    def _normalize_threshold(value):
        normalized = normalize_excel_input(value, "number")
        if isinstance(normalized["value"], bool):
            raise AppActionBlocked("서식 기준값은 숫자여야 합니다.")
        return normalized["value"]

    @staticmethod
    def _normalize_color(value):
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
            raise AppActionBlocked("지원 색상은 노란색·빨간색·초록색·파란색·주황색·회색입니다.")
        return color, COLOR_VALUES[color]

    @staticmethod
    def _normalize_operator(value):
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

    @staticmethod
    def _condition_true(value, operator, threshold):
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

    def _range_cells(self, source):
        cells = source.Cells
        count = int(getattr(cells, "CountLarge", getattr(cells, "Count", 1)))
        for index in range(1, count + 1):
            item_member = getattr(cells, "Item", None)
            yield item_member(index) if callable(item_member) else cells(index)

    def _condition_descriptors(self, source):
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

    @staticmethod
    def _formula_number(value):
        text = str(value or "").strip()
        if text.startswith("="):
            text = text[1:].strip()
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None

    def _has_same_condition(self, descriptors, operator, threshold, color_value):
        expected_operator = CONDITION_OPERATORS[operator]
        for descriptor in descriptors:
            formula_number = self._formula_number(descriptor.get("formula1"))
            if (
                descriptor.get("type") == XL_CELL_VALUE
                and descriptor.get("operator") == expected_operator
                and formula_number is not None
                and math.isclose(float(formula_number), float(threshold))
                and descriptor.get("color") == color_value
            ):
                return True
        return False

    def _prepare_format_in_app(self, application, params, persistent):
        _, sheet, base = self._common_context(application)
        source, source_address, header = self._resolve_source_range(sheet, params)
        count = self._range_count(source)
        if count > MAX_SOURCE_CELLS:
            raise AppActionBlocked(
                f"4단계에서는 한 번에 최대 {MAX_SOURCE_CELLS:,}개 셀까지 검사할 수 있습니다."
            )
        operator = self._normalize_operator(params.get("operator"))
        threshold = self._normalize_threshold(params.get("threshold"))
        color, color_value = self._normalize_color(
            params.get("color") or params.get("fill_color")
        )
        descriptors = self._condition_descriptors(source)
        snapshot = {
            **base,
            "operation": (
                "apply_conditional_format" if persistent else "format_matching_values"
            ),
            "target": source_address,
            "source_range": source_address,
            "source_digest": self._range_digest(source),
            "source_count": count,
            "operator": operator,
            "threshold": threshold,
            "color": color,
            "condition_rules": descriptors,
        }
        original_formats = []
        matches = []
        if persistent:
            noop = self._has_same_condition(
                descriptors, operator, threshold, color_value
            )
            estimated_changes = 0 if noop else 1
        else:
            for cell in self._range_cells(source):
                if self._condition_true(cell.Value2, operator, threshold):
                    matches.append(self._address(cell))
                    original_formats.append({
                        "address": self._address(cell),
                        "color": serializable_excel_value(cell.Interior.Color),
                        "color_index": serializable_excel_value(cell.Interior.ColorIndex),
                    })
            if len(matches) > MAX_DIRECT_FORMAT_CELLS:
                raise AppActionBlocked(
                    f"한 번 표시 방식은 최대 {MAX_DIRECT_FORMAT_CELLS:,}개 셀까지 변경할 수 있습니다. 조건부 서식을 사용해주세요."
                )
            snapshot["matching_cells"] = matches
            snapshot["original_formats"] = original_formats
            noop = not matches
            estimated_changes = len(matches)

        operation = (
            "apply_conditional_format" if persistent else "format_matching_values"
        )
        return PreparedAction(
            app="excel",
            operation=operation,
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
            context_fingerprint=self._state_fingerprint(snapshot),
            prepared_at=self._created_at(),
            noop=noop,
            metadata={"application_hwnd": base["application_hwnd"]},
        )

    def prepare(self, operation: str, params: dict) -> PreparedAction:
        if operation not in self.supported_operations:
            raise AppActionBlocked(f"아직 지원하지 않는 Excel 작업입니다: {operation}")
        if not isinstance(params, dict):
            raise AppActionBlocked("Excel 작업의 params는 객체 형식이어야 합니다.")
        with self._application() as application:
            if operation == "write_cell":
                return self._prepare_write_in_app(application, params)
            if operation == "sum_column_to_cell":
                return self._prepare_sum_in_app(application, params)
            if operation == "format_range":
                return self._prepare_range_format_in_app(application, params)
            if operation == "filter_range":
                return self._prepare_filter_in_app(application, params)
            if operation == "find_replace":
                return self._prepare_find_replace_in_app(application, params)
            if operation == "sort_range":
                return self._prepare_sort_in_app(application, params)
            if operation in {"insert_rows", "insert_columns"}:
                return self._prepare_structure_insert_in_app(
                    application,
                    params,
                    "row" if operation == "insert_rows" else "column",
                )
            return self._prepare_format_in_app(
                application, params, persistent=operation == "apply_conditional_format"
            )

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
        if prepared.app != "excel" or prepared.operation not in self.supported_operations:
            raise AppActionBlocked("지원되는 Excel 작업으로 준비된 요청만 실행할 수 있습니다.")
        with self._application() as application:
            if prepared.operation == "write_cell":
                return self._execute_write(application, prepared)
            if prepared.operation == "sum_column_to_cell":
                return self._execute_sum(application, prepared)
            if prepared.operation == "format_range":
                return self._execute_range_format(application, prepared)
            if prepared.operation == "filter_range":
                return self._execute_filter(application, prepared)
            if prepared.operation == "find_replace":
                return self._execute_find_replace(application, prepared)
            if prepared.operation == "sort_range":
                return self._execute_sort(application, prepared)
            if prepared.operation in {"insert_rows", "insert_columns"}:
                return self._execute_structure_insert(application, prepared)
            if prepared.operation == "apply_conditional_format":
                return self._execute_conditional_format(application, prepared)
            return self._execute_direct_format(application, prepared)

    def _execute_write(self, application, prepared):
        _, _, cell, snapshot = self._active_context(application, prepared.target)
        if self._state_fingerprint(snapshot) != prepared.context_fingerprint:
            raise AppActionContextChanged(
                "확인 이후 Excel 통합문서·시트 또는 셀 상태가 바뀌어 실행하지 않았습니다."
            )
        desired = {
            "kind": prepared.params["value_type"],
            "value": prepared.params["value"],
        }
        if prepared.noop:
            return self._result(prepared, snapshot, snapshot, changed=False)
        return self._write_and_verify(cell, snapshot, desired, prepared)

    def _execute_sum(self, application, prepared):
        current = self._prepare_sum_in_app(application, prepared.params)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        cell = sheet.Range(current.target)
        before = {
            "current_formula": current.current_state.get("formula"),
            "current_value": current.current_state.get("value"),
        }
        if current.noop:
            return self._result(current, before, before, changed=False)
        desired = {
            "kind": current.params["value_type"],
            "value": current.params["value"],
        }
        return self._write_and_verify(cell, before, desired, current)

    def _execute_structure_insert(self, application, prepared):
        current = self._prepare_structure_insert_in_app(
            application,
            prepared.params,
            prepared.params["axis"],
        )
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        axis = current.params["axis"]
        index = int(current.params["index"])
        count = int(current.params["count"])
        first_column = int(current.params["used_first_column"])
        first_row = int(current.params["used_first_row"])
        last_column = int(current.params["used_last_column"])
        last_row = int(current.params["used_last_row"])
        inserted = None
        try:
            if axis == "row":
                inserted = sheet.Rows(f"{index}:{index + count - 1}")
                inserted.Insert()
                shifted = sheet.Range(
                    f"{excel_column_letters(first_column)}{index + count}:"
                    f"{excel_column_letters(last_column)}{last_row + count}"
                )
                blank = sheet.Range(
                    f"{excel_column_letters(first_column)}{index}:"
                    f"{excel_column_letters(last_column)}{index + count - 1}"
                )
            else:
                first = excel_column_letters(index)
                last = excel_column_letters(index + count - 1)
                inserted = sheet.Columns(f"{first}:{last}")
                inserted.Insert()
                shifted = sheet.Range(
                    f"{excel_column_letters(index + count)}{first_row}:"
                    f"{excel_column_letters(last_column + count)}{last_row}"
                )
                blank = sheet.Range(f"{first}{first_row}:{last}{last_row}")
            if self._range_digest(shifted) != current.params["shifted_digest"]:
                raise AppActionVerificationError(
                    "Excel 행·열 추가 후 기존 데이터의 이동 결과가 예상과 다릅니다."
                )
            if not self._range_is_blank(blank):
                raise AppActionVerificationError(
                    "Excel에 추가된 행·열이 비어 있지 않아 결과를 승인할 수 없습니다."
                )
        except Exception as error:
            if inserted is not None:
                try:
                    inserted.Delete()
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 행·열 추가 또는 검증에 실패했습니다."
            ) from error
        return self._result(
            current,
            {"used_address": current.current_state["used_address"]},
            {"inserted": count, "axis": axis},
            changed=True,
        )

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

    def _execute_conditional_format(self, application, prepared):
        current = self._prepare_format_in_app(application, prepared.params, True)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        source = sheet.Range(current.target)
        before = {"condition_count": current.current_state["condition_count"]}
        if current.noop:
            return self._result(current, before, before, changed=False)
        condition = None
        try:
            condition = source.FormatConditions.Add(
                Type=XL_CELL_VALUE,
                Operator=CONDITION_OPERATORS[current.params["operator"]],
                Formula1=str(current.params["threshold"]),
            )
            condition.Interior.Color = current.params["color_value"]
            descriptors = self._condition_descriptors(source)
            if not self._has_same_condition(
                descriptors,
                current.params["operator"],
                current.params["threshold"],
                current.params["color_value"],
            ):
                raise AppActionVerificationError("추가한 Excel 조건부 서식을 다시 찾지 못했습니다.")
            after = {"condition_count": len(descriptors)}
        except Exception as error:
            try:
                if condition is not None:
                    condition.Delete()
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError("Excel 조건부 서식 적용 또는 검증에 실패했습니다.") from error
        return self._result(current, before, after, changed=True)

    def _execute_direct_format(self, application, prepared):
        current = self._prepare_format_in_app(application, prepared.params, False)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        originals = current.params["original_formats"]
        before = {"matching_count": len(originals)}
        if current.noop:
            return self._result(current, before, before, changed=False)
        changed_cells = []
        try:
            for original in originals:
                cell = sheet.Range(original["address"])
                cell.Interior.Color = current.params["color_value"]
                changed_cells.append((cell, original))
            for cell, _ in changed_cells:
                if int(cell.Interior.Color) != int(current.params["color_value"]):
                    raise AppActionVerificationError(
                        f"Excel {self._address(cell)} 셀의 채우기 색을 확인하지 못했습니다."
                    )
            after = {"matching_count": len(changed_cells), "color": current.params["color"]}
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
            raise AppActionVerificationError("Excel 셀 표시 또는 검증에 실패했습니다.") from error
        return self._result(current, before, after, changed=True)

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

    def _execute_range_format(self, application, prepared):
        current = self._prepare_range_format_in_app(application, prepared.params)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        source = sheet.Range(current.target)
        before = {
            "cell_count": current.current_state["cell_count"],
            "changing_count": current.current_state["changing_count"],
        }
        if current.noop:
            return self._result(current, before, before, changed=False)
        try:
            self._apply_desired_format(source, current.params["desired"])
            for cell in self._range_cells(source):
                state = self._format_state(cell)
                if not self._format_state_matches(
                    state, current.params["desired"]
                ):
                    raise AppActionVerificationError(
                        f"Excel {state['address']} 셀의 서식 적용 결과가 요청과 다릅니다."
                    )
            after = {
                "cell_count": current.current_state["cell_count"],
                "changed_count": current.current_state["changing_count"],
                "desired": current.params["desired"],
            }
        except Exception as error:
            for original in reversed(current.params["original_formats"]):
                try:
                    self._restore_format(sheet.Range(original["address"]), original)
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 범위 서식 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, changed=True)

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

    def _execute_filter(self, application, prepared):
        current = self._prepare_filter_in_app(application, prepared.params)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        previous = current.params["previous_filter"]
        before = {
            "filter_active": current.current_state["filter_active"],
            "active_filter_count": current.current_state["active_filter_count"],
        }
        if current.noop:
            return self._result(current, before, before, changed=False)
        try:
            if current.params["clear"]:
                sheet.ShowAllData()
                after_state = self._filter_state(sheet)
                if after_state.get("filter_mode"):
                    raise AppActionVerificationError("Excel 필터 해제 결과를 확인하지 못했습니다.")
            else:
                source = sheet.Range(current.params["table_range"])
                source.AutoFilter(
                    Field=current.params["field_index"],
                    Criteria1=current.params["criteria"],
                )
                after_state = self._filter_state(sheet)
                if not self._matching_filter(
                    after_state,
                    current.params["table_range"],
                    current.params["field_index"],
                    current.params["criteria"],
                ):
                    raise AppActionVerificationError("Excel 필터 적용 결과를 확인하지 못했습니다.")
            after = {
                "filter_active": bool(after_state.get("filter_mode")),
                "active_filter_count": sum(
                    1 for item in after_state.get("filters", []) if item.get("on")
                ),
            }
        except Exception as error:
            try:
                self._restore_filter_state(sheet, previous)
            except Exception:
                pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 필터 적용 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, changed=True)

    def _execute_find_replace(self, application, prepared):
        current = self._prepare_find_replace_in_app(application, prepared.params)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        before = {"matching_count": current.current_state["matching_count"]}
        if current.noop:
            return self._result(current, before, before, changed=False)
        changed = []
        try:
            for match in current.params["matches"]:
                cell = sheet.Range(match["address"])
                cell.Value2 = match["replacement"]
                changed.append((cell, match))
            for cell, match in changed:
                if not excel_values_equal(cell.Value2, match["replacement"]):
                    raise AppActionVerificationError(
                        f"Excel {match['address']} 셀의 바꾸기 결과가 요청과 다릅니다."
                    )
            after = {"replaced_count": len(changed)}
        except Exception as error:
            for cell, match in reversed(changed):
                try:
                    cell.Value2 = match["original"]
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 찾기·바꾸기 실행 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, changed=True)

    @staticmethod
    def _row_multiset(rows):
        return sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )

    def _execute_sort(self, application, prepared):
        current = self._prepare_sort_in_app(application, prepared.params)
        self._ensure_same_context(current, prepared)
        _, sheet, _ = self._common_context(application)
        before = {
            "row_count": current.current_state["row_count"],
            "column_count": current.current_state["column_count"],
        }
        if current.noop:
            return self._result(current, before, before, changed=False)
        first_column, _, last_column, last_row = self._range_bounds(current.target)
        column = excel_column_letters(current.params["column_number"])
        # Excel may ignore Header=xlYes when Key1 itself includes the header
        # cell, which can move the header into the sorted data on real Office
        # installations.  Exclude the header from both the source and key so
        # it is structurally impossible for the header row to move.
        key_range = sheet.Range(f"{column}2:{column}{last_row}")
        source = sheet.Range(
            f"{excel_column_letters(first_column)}2:"
            f"{excel_column_letters(last_column)}{last_row}"
        )
        sort_completed = False
        try:
            source.Sort(
                Key1=key_range,
                Order1=(
                    XL_DESCENDING
                    if current.params["direction"] == "descending"
                    else XL_ASCENDING
                ),
                Header=XL_NO,
                Orientation=XL_SORT_COLUMNS,
            )
            sort_completed = True
            table = self._resolve_table_range(
                sheet,
                {"table_range": current.target},
                current.params["column_name"],
            )
            rows = self._table_values(sheet, table)
            key_index = table["column_number"] - table["first_column"]
            actual_keys = [row[key_index] for row in rows]
            if actual_keys != current.params["expected_keys"]:
                raise AppActionVerificationError("Excel 정렬 순서가 요청과 다릅니다.")
            if self._row_multiset(rows) != self._row_multiset(
                current.params["original_rows"]
            ):
                raise AppActionVerificationError("정렬 후 표의 행 데이터 구성이 달라졌습니다.")
            after = {
                "row_count": len(rows),
                "direction": current.params["direction"],
                "verified_whole_rows": True,
            }
        except Exception as error:
            if sort_completed:
                try:
                    application.Undo()
                except Exception:
                    pass
            if isinstance(error, AppActionError):
                raise
            raise AppActionVerificationError(
                "Excel 표 정렬 실행 또는 검증에 실패했습니다."
            ) from error
        return self._result(current, before, after, changed=True)

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

    def _undo_cell_write(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        cell = sheet.Range(prepared.target)
        current = self._cell_snapshot(cell)
        desired = {
            "kind": prepared.params["value_type"],
            "value": prepared.params["value"],
        }
        if not self._matches_desired(current, desired):
            raise AppActionContextChanged(
                "직전 편집 뒤 대상 셀 값이 달라져 안전하게 복원하지 않았습니다."
            )
        original = {
            "formula": prepared.current_state.get("formula"),
            "value": prepared.current_state.get("value"),
        }
        self._restore_cell_value(cell, original)
        restored = self._cell_snapshot(cell)
        expected = {
            "kind": "formula" if original["formula"] is not None else "value",
            "value": (
                original["formula"]
                if original["formula"] is not None
                else original["value"]
            ),
        }
        if not self._matches_desired(restored, expected):
            raise AppActionVerificationError(
                "Excel 셀의 원래 값이 복원되었는지 확인하지 못했습니다."
            )
        return restored

    def _undo_range_format(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        desired = dict(prepared.params.get("desired") or {})
        originals = list(prepared.params.get("original_formats") or [])
        for original in originals:
            current = self._format_state(sheet.Range(original["address"]))
            if not self._format_state_matches(current, desired):
                raise AppActionContextChanged(
                    "직전 편집 뒤 범위 서식이 달라져 안전하게 복원하지 않았습니다."
                )
        for original in originals:
            self._restore_format(sheet.Range(original["address"]), original)
        restored = []
        for original in originals:
            state = self._format_state(sheet.Range(original["address"]))
            if not self._format_state_matches(state, original):
                raise AppActionVerificationError(
                    f"Excel {original['address']} 셀의 원래 서식을 확인하지 못했습니다."
                )
            restored.append(state)
        return {"restored_count": len(restored)}

    def _undo_find_replace(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        matches = list(prepared.params.get("matches") or [])
        for match in matches:
            cell = sheet.Range(match["address"])
            if not excel_values_equal(cell.Value2, match["replacement"]):
                raise AppActionContextChanged(
                    "직전 찾기·바꾸기 뒤 셀 값이 달라져 복원하지 않았습니다."
                )
        for match in matches:
            sheet.Range(match["address"]).Value2 = match["original"]
        for match in matches:
            if not excel_values_equal(
                sheet.Range(match["address"]).Value2,
                match["original"],
            ):
                raise AppActionVerificationError(
                    f"Excel {match['address']} 셀의 원래 값을 확인하지 못했습니다."
                )
        return {"restored_count": len(matches)}

    def _undo_sort(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        table = self._resolve_table_range(
            sheet,
            {"table_range": prepared.target},
            prepared.params.get("column_name"),
        )
        current_rows = self._table_values(sheet, table)
        key_index = table["column_number"] - table["first_column"]
        if (
            self._row_multiset(current_rows)
            != self._row_multiset(prepared.params["original_rows"])
            or [row[key_index] for row in current_rows]
            != prepared.params["expected_keys"]
        ):
            raise AppActionContextChanged(
                "직전 정렬 뒤 표 데이터가 달라져 안전하게 복원하지 않았습니다."
            )

        original_cells = list(prepared.params.get("original_cells") or [])
        for row_offset, original_row in enumerate(prepared.params["original_rows"]):
            row_number = row_offset + 2
            for column_offset, value in enumerate(original_row):
                column_number = table["first_column"] + column_offset
                cell = sheet.Cells(row_number, column_number)
                if original_cells:
                    self._restore_cell_value(
                        cell,
                        original_cells[row_offset][column_offset],
                    )
                else:
                    cell.Value2 = value
        restored_rows = self._table_values(sheet, table)
        if restored_rows != prepared.params["original_rows"]:
            raise AppActionVerificationError(
                "Excel 표의 정렬 전 행 순서가 복원되었는지 확인하지 못했습니다."
            )
        if original_cells:
            for row_offset, row_snapshot in enumerate(original_cells):
                for column_offset, snapshot in enumerate(row_snapshot):
                    cell = sheet.Cells(
                        row_offset + 2,
                        table["first_column"] + column_offset,
                    )
                    formula = str(cell.Formula) if bool(cell.HasFormula) else None
                    if not excel_formulas_equal(formula, snapshot.get("formula")):
                        raise AppActionVerificationError(
                            "Excel 표의 정렬 전 수식이 복원되었는지 확인하지 못했습니다."
                        )
        return {"restored_rows": len(restored_rows)}

    def _undo_structure_insert(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        axis = prepared.params["axis"]
        index = int(prepared.params["index"])
        count = int(prepared.params["count"])
        first_column = int(prepared.params["used_first_column"])
        first_row = int(prepared.params["used_first_row"])
        last_column = int(prepared.params["used_last_column"])
        last_row = int(prepared.params["used_last_row"])
        if axis == "row":
            shifted = sheet.Range(
                f"{excel_column_letters(first_column)}{index + count}:"
                f"{excel_column_letters(last_column)}{last_row + count}"
            )
            blank = sheet.Range(
                f"{excel_column_letters(first_column)}{index}:"
                f"{excel_column_letters(last_column)}{index + count - 1}"
            )
            inserted = sheet.Rows(f"{index}:{index + count - 1}")
            original_address = (
                f"{excel_column_letters(first_column)}{index}:"
                f"{excel_column_letters(last_column)}{last_row}"
            )
        else:
            first = excel_column_letters(index)
            last = excel_column_letters(index + count - 1)
            shifted = sheet.Range(
                f"{excel_column_letters(index + count)}{first_row}:"
                f"{excel_column_letters(last_column + count)}{last_row}"
            )
            blank = sheet.Range(f"{first}{first_row}:{last}{last_row}")
            inserted = sheet.Columns(f"{first}:{last}")
            original_address = (
                f"{excel_column_letters(index)}{first_row}:"
                f"{excel_column_letters(last_column)}{last_row}"
            )
        if (
            self._range_digest(shifted) != prepared.params["shifted_digest"]
            or not self._range_is_blank(blank)
        ):
            raise AppActionContextChanged(
                "삽입된 행·열 또는 이동된 데이터가 달라져 안전하게 복원하지 않았습니다."
            )
        inserted.Delete()
        restored_digest = self._range_digest(sheet.Range(original_address))
        if restored_digest != prepared.params["shifted_digest"]:
            raise AppActionVerificationError(
                "Excel 행·열 삭제 뒤 원래 데이터 위치를 확인하지 못했습니다."
            )
        return {"restored_axis": axis, "restored_count": count}

    def _undo_filter(self, application, prepared):
        sheet = self._ensure_undo_context(application, prepared)
        current = self._filter_state(sheet)
        if prepared.params.get("clear"):
            matches_after = not current.get("filter_mode")
        else:
            matches_after = self._matching_filter(
                current,
                prepared.params["table_range"],
                prepared.params["field_index"],
                prepared.params["criteria"],
            )
        if not matches_after:
            raise AppActionContextChanged(
                "직전 편집 뒤 필터 상태가 달라져 안전하게 복원하지 않았습니다."
            )
        previous = dict(prepared.params.get("previous_filter") or {})
        self._restore_filter_state(sheet, previous)
        restored = self._filter_state(sheet)
        if self._state_fingerprint({"filter": restored}) != self._state_fingerprint(
            {"filter": previous}
        ):
            raise AppActionVerificationError(
                "Excel의 이전 필터 상태가 복원되었는지 확인하지 못했습니다."
            )
        return {"filter_state": restored}

    def undo(self, prepared, record=None):
        """Restore one verified Excel edit from its structured snapshot."""
        if not isinstance(prepared, PreparedAction):
            prepared = PreparedAction.from_dict(prepared or {})
        if prepared.app != "excel" or not prepared.reversible:
            raise AppActionBlocked("복원할 수 있는 Excel 편집 작업이 아닙니다.")
        handlers = {
            "write_cell": self._undo_cell_write,
            "sum_column_to_cell": self._undo_cell_write,
            "format_range": self._undo_range_format,
            "find_replace": self._undo_find_replace,
            "sort_range": self._undo_sort,
            "insert_rows": self._undo_structure_insert,
            "insert_columns": self._undo_structure_insert,
            "filter_range": self._undo_filter,
        }
        handler = handlers.get(prepared.operation)
        if handler is None:
            raise AppActionBlocked("이 Excel 작업은 자동 복원을 지원하지 않습니다.")
        try:
            with self._application() as application:
                restored = handler(application, prepared)
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
