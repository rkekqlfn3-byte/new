"""Bounded Excel VBA inspection, backup, editing, and explicit execution."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from engine.app_actions.base import (
    AppActionAmbiguousTarget,
    AppActionBlocked,
    AppActionContextChanged,
    AppActionError,
    AppActionVerificationError,
    PreparedAction,
)
from engine.app_actions.excel_adapter import ExcelAdapter
from engine.app_actions.office_helpers import (
    prepared_at_timestamp,
    stable_state_fingerprint,
)
from engine.runtime_paths import USER_DATA_DIR


VBA_DOCUMENT_EXTENSIONS = frozenset({".xlsm", ".xlsb"})
VBA_OPERATIONS = frozenset({
    "vba_inspect_project",
    "vba_read_module",
    "vba_analyze_module",
    "vba_replace_module",
    "vba_run_procedure",
})
VBEXT_CT_STDMODULE = 1
VBEXT_PP_LOCKED = 1
MAX_VBA_MODULE_CHARS = 50_000
MAX_VBA_MODULES = 100
MAX_VBA_FINDINGS = 50
MAX_VBA_DIFF_CHARS = 12_000
OFFICE_SECURITY_VERSIONS = ("16.0", "15.0", "14.0")

COMPONENT_TYPE_NAMES = {
    1: "standard_module",
    2: "class_module",
    3: "user_form",
    100: "document_module",
}

PROCEDURE_PATTERN = re.compile(
    r"^\s*(?:(Public|Private|Friend|Static)\s+)?"
    r"(Sub|Function|Property\s+(?:Get|Let|Set))\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)


class VbaTrustAccessBlocked(AppActionBlocked):
    """Excel refused programmatic VBProject access before any mutation."""

    error_type = "environment_error"
    status = "blocked"
    retryable = False
    state_changed = False
    blocked_reason = "vba_trust_access"

    def __init__(self, message=None, *, trust_status=None):
        super().__init__(
            message
            or "Excel이 VBA 프로젝트 접근을 차단했습니다. 사용자가 Excel 보안 센터의 "
            "'VBA 프로젝트 개체 모델에 안전하게 액세스'를 직접 허용한 뒤 "
            "Excel을 다시 시작해야 합니다. JARVIS는 이 보안 설정을 변경하지 않습니다."
        )
        self.trust_status = dict(trust_status or vba_trust_status())
        self.diagnostic_context = {
            "blocked_reason": self.blocked_reason,
            "trust_status": self.trust_status.get("status", "unknown"),
            "security_setting_changed": False,
        }


def vba_trust_status(registry_module=None) -> dict:
    """Read AccessVBOM configuration without starting Excel or changing it."""
    if registry_module is None:
        try:
            import winreg as registry_module
        except ImportError:
            return {
                "status": "unavailable",
                "configured": False,
                "source": None,
                "scope": None,
                "office_version": None,
                "runtime_check_required": True,
                "security_setting_changed": False,
            }
    candidates = []
    for source, template in (
        (
            "group_policy",
            r"Software\Policies\Microsoft\Office\{}\excel\security",
        ),
        (
            "user_setting",
            r"Software\Microsoft\Office\{}\Excel\Security",
        ),
    ):
        for version in OFFICE_SECURITY_VERSIONS:
            for scope, root in (
                ("current_user", registry_module.HKEY_CURRENT_USER),
                ("local_machine", registry_module.HKEY_LOCAL_MACHINE),
            ):
                candidates.append((source, version, scope, root, template.format(version)))
    views = tuple(dict.fromkeys((
        0,
        int(getattr(registry_module, "KEY_WOW64_64KEY", 0) or 0),
        int(getattr(registry_module, "KEY_WOW64_32KEY", 0) or 0),
    )))
    for source, version, scope, root, path in candidates:
        for view in views:
            try:
                with registry_module.OpenKey(
                    root,
                    path,
                    0,
                    registry_module.KEY_READ | view,
                ) as key:
                    value, _ = registry_module.QueryValueEx(key, "AccessVBOM")
            except (OSError, ValueError, TypeError):
                continue
            try:
                enabled = int(value) == 1
            except (TypeError, ValueError):
                enabled = False
            return {
                "status": "enabled" if enabled else "disabled",
                "configured": True,
                "source": source,
                "scope": scope,
                "office_version": version,
                # Registry configuration is informative; COM remains the
                # authority because policy and a running Excel can differ.
                "runtime_check_required": True,
                "security_setting_changed": False,
            }
    return {
        "status": "not_configured",
        "configured": False,
        "source": None,
        "scope": None,
        "office_version": None,
        "runtime_check_required": True,
        "security_setting_changed": False,
    }


def _is_vba_trust_error(error) -> bool:
    pending = [error]
    visited = set()
    text_parts = []
    while pending:
        value = pending.pop()
        marker = id(value)
        if marker in visited:
            continue
        visited.add(marker)
        if isinstance(value, int):
            if value == -2146827284:
                return True
            continue
        if isinstance(value, str):
            text_parts.append(value.casefold())
            continue
        if isinstance(value, dict):
            pending.extend(value.values())
            continue
        if isinstance(value, (tuple, list)):
            pending.extend(value)
            continue
        text_parts.append(str(value).casefold())
        if isinstance(value, BaseException):
            pending.extend(getattr(value, "args", ()))
            cause = getattr(value, "__cause__", None)
            if cause is not None:
                pending.append(cause)
    text = " ".join(text_parts)
    return any(term in text for term in (
        "programmatic access",
        "visual basic project",
        "vbproject",
        "프로그래밍 방식으로 액세스",
        "vba 프로젝트 개체 모델",
    ))


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()


def _normalized_path(value) -> str:
    return os.path.normcase(os.path.abspath(str(value or "")))


def _trim(value, limit=500) -> str:
    text = str(value or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_vba_procedures(code: str) -> list[dict]:
    """Return a JSON-only outline of top-level VBA procedures."""
    procedures = []
    for line_number, line in enumerate(str(code or "").splitlines(), start=1):
        if line.lstrip().startswith("'"):
            continue
        match = PROCEDURE_PATTERN.match(line)
        if not match:
            continue
        visibility = str(match.group(1) or "Public").title()
        kind = re.sub(r"\s+", " ", match.group(2).title())
        parameters = str(match.group(4) or "").strip()
        procedures.append({
            "name": match.group(3),
            "kind": kind,
            "visibility": visibility,
            "parameters": parameters,
            "line": line_number,
            "runnable": (
                visibility.casefold() == "public"
                and kind.casefold() == "sub"
                and not parameters
            ),
        })
    return procedures


def analyze_vba_code(code: str) -> dict:
    """Conservative local analysis; code is never sent to or executed by this scan."""
    source = str(code or "")
    findings = []
    capabilities = set()

    rules = (
        (r"\bShell\s*(?:\(|[\"'])", "critical", "shell", "외부 프로그램을 실행하는 Shell 호출이 있습니다."),
        (r"\bCreateObject\s*\(\s*[\"']WScript\.Shell", "critical", "shell", "WScript.Shell 객체를 생성합니다."),
        (r"\b(?:Kill|RmDir)\s+", "critical", "file_delete", "파일 또는 폴더 삭제 동작이 있습니다."),
        (r"\b(?:XMLHTTP|WinHttp|ServerXMLHTTP)\b", "critical", "network", "네트워크 요청 객체를 사용합니다."),
        (
            r"\b(?:SaveSetting|GetSetting|DeleteSetting|RegWrite|RegDelete)\b",
            "critical", "registry", "레지스트리 설정을 읽거나 변경합니다.",
        ),
        (r"\bOpen\s+.+\s+For\s+(?:Output|Append|Binary)\b", "high", "file_write", "파일 쓰기 구문이 있습니다."),
        (r"\bDeclare\s+(?:PtrSafe\s+)?(?:Function|Sub)\b", "high", "native_api", "Windows 네이티브 API 선언이 있습니다."),
        (r"\bOn\s+Error\s+Resume\s+Next\b", "medium", "error_suppression", "오류를 무시해 실제 실패가 숨겨질 수 있습니다."),
        (r"(?<!\.)\bRows\.Count\b", "medium", "unqualified_rows", "Rows.Count가 시트로 한정되지 않아 활성 시트에 따라 달라질 수 있습니다."),
        (
            r"\b(?:Select|Activate)\b", "low", "selection_dependency",
            "Select/Activate 의존으로 현재 선택 상태에 따라 결과가 달라질 수 있습니다.",
        ),
    )
    lines = source.splitlines()
    for pattern, severity, capability, message in rules:
        expression = re.compile(pattern, re.IGNORECASE)
        for line_number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("'") or not expression.search(line):
                continue
            findings.append({
                "severity": severity,
                "capability": capability,
                "line": line_number,
                "message": message,
                "preview": _trim(line, 180),
            })
            capabilities.add(capability)
            if len(findings) >= MAX_VBA_FINDINGS:
                break
        if len(findings) >= MAX_VBA_FINDINGS:
            break

    procedures = parse_vba_procedures(source)
    behavior = []
    behavior_rules = (
        (r"\b(?:For|For\s+Each|Do|While)\b", "반복문으로 여러 항목을 처리합니다."),
        (r"\b(?:Range|Cells)\s*\(", "Excel 셀 또는 범위를 읽거나 변경합니다."),
        (r"\bWorksheets?\s*\(", "워크시트를 참조합니다."),
        (r"\b(?:Copy|PasteSpecial)\b", "데이터나 시트를 복사합니다."),
        (r"\b(?:Save|SaveAs)\b", "통합문서 저장 동작이 있습니다."),
    )
    for pattern, label in behavior_rules:
        if re.search(pattern, source, re.IGNORECASE):
            behavior.append(label)

    names = ", ".join(item["name"] for item in procedures[:10]) or "없음"
    explanation = (
        f"프로시저 {len(procedures)}개({names})를 포함합니다. "
        + (" ".join(behavior) if behavior else "뚜렷한 Excel 조작 패턴은 확인되지 않았습니다.")
    )
    return {
        "procedures": procedures,
        "explanation": explanation,
        "findings": findings,
        "dangerous_capabilities": sorted(
            capability
            for capability in capabilities
            if capability in {"shell", "file_delete", "network", "registry"}
        ),
    }


class ExcelVbaAdapter(ExcelAdapter):
    """ExcelAdapter plus a separately allowlisted VBA surface."""

    supported_operations = ExcelAdapter.supported_operations | VBA_OPERATIONS

    def __init__(self, *args, backup_dir=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._vba_backup_dir = Path(
            backup_dir or (Path(USER_DATA_DIR) / "vba_backups")
        )

    def _targeted_clone_kwargs(self) -> dict:
        return {"backup_dir": self._vba_backup_dir}

    @staticmethod
    def _item(collection, index):
        item = getattr(collection, "Item", None)
        if item is None:
            return collection(index)
        return item(index)

    def _workbook_context(self, application, params, *, require_write=False):
        workbook = getattr(application, "ActiveWorkbook", None)
        if workbook is None:
            raise AppActionBlocked("Excel에서 VBA를 확인할 통합문서를 먼저 활성화해주세요.")
        document_id = self._document_id(workbook)
        expected = str(params.get("document_path") or "").strip()
        if expected and _normalized_path(document_id) != _normalized_path(expected):
            raise AppActionContextChanged(
                "연결된 파일과 현재 활성 Excel 통합문서가 달라 VBA 작업을 차단했습니다."
            )
        extension = Path(document_id).suffix.casefold()
        if extension not in VBA_DOCUMENT_EXTENSIONS:
            raise AppActionBlocked(
                "9단계 VBA 작업은 매크로 사용 통합문서(.xlsm, .xlsb)에서만 지원합니다."
            )
        if require_write and bool(getattr(workbook, "ReadOnly", False)):
            raise AppActionBlocked("현재 통합문서는 읽기 전용이라 VBA 코드를 변경할 수 없습니다.")
        sheet = getattr(application, "ActiveSheet", None)
        return workbook, {
            "document_id": document_id,
            "workbook_name": str(getattr(workbook, "Name", "") or ""),
            "sheet": str(getattr(sheet, "Name", "") or ""),
            "extension": extension,
            "read_only": bool(getattr(workbook, "ReadOnly", False)),
            "has_vba_project": bool(getattr(workbook, "HasVBProject", False)),
            "application_hwnd": int(getattr(application, "Hwnd", 0) or 0),
        }

    @staticmethod
    def _project(workbook):
        if not bool(getattr(workbook, "HasVBProject", False)):
            return None
        try:
            project = workbook.VBProject
            protection = int(getattr(project, "Protection", 0) or 0)
        except Exception as error:
            if _is_vba_trust_error(error):
                raise VbaTrustAccessBlocked() from error
            raise AppActionBlocked(
                "Excel VBA 프로젝트를 읽는 중 예기치 않은 오류가 발생했습니다. "
                "통합문서를 다시 연 뒤 재시도해주세요."
            ) from error
        if protection == VBEXT_PP_LOCKED:
            raise AppActionBlocked(
                "VBA 프로젝트가 암호로 잠겨 있어 모듈을 읽거나 변경하지 않았습니다."
            )
        return project

    def _component_records(self, project, *, include_code=False):
        if project is None:
            return []
        try:
            components = project.VBComponents
            count = int(components.Count)
        except Exception as error:
            if _is_vba_trust_error(error):
                raise VbaTrustAccessBlocked() from error
            raise AppActionBlocked(
                "Excel VBA 모듈 목록을 읽지 못했습니다. 프로젝트 상태를 확인해주세요."
            ) from error
        if count > MAX_VBA_MODULES:
            raise AppActionBlocked(
                f"VBA 모듈은 한 프로젝트에서 최대 {MAX_VBA_MODULES}개까지 확인합니다."
            )
        records = []
        for index in range(1, count + 1):
            component = self._item(components, index)
            code = self._component_code(component)
            record = {
                "name": str(component.Name),
                "type": int(component.Type),
                "type_name": COMPONENT_TYPE_NAMES.get(int(component.Type), "unknown"),
                "line_count": len(code.splitlines()),
                "code_digest": _digest(code),
                "procedures": parse_vba_procedures(code),
            }
            if include_code:
                record["code"] = code
            records.append(record)
        return records

    @staticmethod
    def _component_code(component) -> str:
        module = component.CodeModule
        count = int(getattr(module, "CountOfLines", 0) or 0)
        code = str(module.Lines(1, count) if count else "")
        if len(code) > MAX_VBA_MODULE_CHARS:
            raise AppActionBlocked(
                f"VBA 모듈 코드는 최대 {MAX_VBA_MODULE_CHARS:,}자까지 처리합니다."
            )
        return code

    def _select_component(
        self,
        project,
        module_name=None,
        *,
        procedure_name=None,
        standard_only=False,
    ):
        records = self._component_records(project, include_code=True)
        candidates = records
        if standard_only:
            candidates = [item for item in candidates if item["type"] == VBEXT_CT_STDMODULE]
        wanted_module = str(module_name or "").strip().casefold()
        wanted_procedure = str(procedure_name or "").strip().casefold()
        if wanted_module:
            candidates = [
                item for item in candidates if item["name"].casefold() == wanted_module
            ]
        if wanted_procedure:
            candidates = [
                item
                for item in candidates
                if any(
                    procedure["name"].casefold() == wanted_procedure
                    for procedure in item["procedures"]
                )
            ]
        if not candidates:
            target = module_name or procedure_name or "요청한 VBA 대상"
            raise AppActionBlocked(f"{target}을 현재 VBA 프로젝트에서 찾지 못했습니다.")
        if len(candidates) > 1:
            names = [item["name"] for item in candidates]
            raise AppActionAmbiguousTarget(
                "VBA 모듈 대상이 여러 개입니다. 모듈 이름을 명령에 포함해주세요: "
                + ", ".join(names),
                candidates=names,
                target_name=str(module_name or procedure_name or "VBA module"),
            )
        selected = candidates[0]
        components = project.VBComponents
        return self._item(components, selected["name"]), selected

    @staticmethod
    def _project_snapshot(base, project, records):
        return {
            **base,
            "project_name": str(getattr(project, "Name", "") or "") if project else "",
            "modules": [
                {
                    "name": item["name"],
                    "type": item["type"],
                    "line_count": item["line_count"],
                    "code_digest": item["code_digest"],
                }
                for item in records
            ],
        }

    @staticmethod
    def _prepared(
        operation,
        base,
        target,
        params,
        current_state,
        snapshot,
        *,
        destructive=False,
        reversible=False,
        noop=False,
        metadata=None,
    ):
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=base["document_id"],
            workbook_name=base["workbook_name"],
            sheet=base["sheet"],
            target=target,
            params=params,
            current_state=current_state,
            estimated_changes=0 if noop else (1 if destructive else 0),
            destructive=bool(destructive),
            reversible=bool(reversible),
            verification_method={
                "vba_inspect_project": "read_vba_project_outline",
                "vba_read_module": "read_vba_module_digest",
                "vba_analyze_module": "static_vba_analysis",
                "vba_replace_module": "read_replaced_vba_module_digest",
                "vba_run_procedure": (
                    "excel_application_run_returned_without_com_error"
                ),
            }[operation],
            context_fingerprint=stable_state_fingerprint(snapshot),
            prepared_at=prepared_at_timestamp(),
            noop=bool(noop),
            metadata={"application_hwnd": base["application_hwnd"], **dict(metadata or {})},
        )

    def _prepare_inspect(self, application, params):
        workbook, base = self._workbook_context(application, params)
        project = self._project(workbook)
        records = self._component_records(project)
        snapshot = self._project_snapshot(base, project, records)
        return self._prepared(
            "vba_inspect_project",
            base,
            "VBA project",
            {"project": snapshot},
            {
                "has_vba_project": base["has_vba_project"],
                "project_name": snapshot["project_name"],
                "module_count": len(records),
                "modules": records,
            },
            snapshot,
        )

    def _prepare_module_read(self, application, operation, params):
        workbook, base = self._workbook_context(application, params)
        project = self._project(workbook)
        if project is None:
            raise AppActionBlocked("현재 통합문서에 읽을 VBA 프로젝트가 없습니다.")
        _, selected = self._select_component(
            project,
            params.get("module_name"),
            procedure_name=params.get("procedure_name"),
        )
        analysis = analyze_vba_code(selected["code"])
        snapshot = {
            **base,
            "operation": operation,
            "project_name": str(project.Name),
            "module_name": selected["name"],
            "module_type": selected["type"],
            "code_digest": selected["code_digest"],
        }
        return self._prepared(
            operation,
            base,
            selected["name"],
            {
                "module_name": selected["name"],
                "code": selected["code"],
                "analysis": analysis,
            },
            {
                "module_name": selected["name"],
                "module_type": selected["type_name"],
                "line_count": selected["line_count"],
                "code_digest": selected["code_digest"],
                "procedures": selected["procedures"],
                "analysis": analysis,
            },
            snapshot,
            metadata={"dangerous_capabilities": analysis["dangerous_capabilities"]},
        )

    @staticmethod
    def _exact_replacement(code, find, replacement):
        old = str(find or "")
        if not old:
            raise AppActionBlocked("교체할 기존 VBA 코드 조각은 비워둘 수 없습니다.")
        count = code.count(old)
        if count != 1:
            raise AppActionBlocked(
                f"기존 코드 조각이 {count}개 발견됐습니다. 정확히 한 곳만 일치하도록 더 길게 지정해주세요."
            )
        return code.replace(old, str(replacement or ""), 1)

    @staticmethod
    def _patch_last_row(code):
        pattern = re.compile(
            r"(?<![A-Za-z0-9_.])Cells\s*\(\s*Rows\.Count\s*,\s*([^\)]+)\)"
            r"\.End\s*\(\s*xlUp\s*\)\.Row",
            re.IGNORECASE,
        )
        matches = list(pattern.finditer(code))
        if len(matches) != 1:
            raise AppActionBlocked(
                "안전하게 고칠 수 있는 단일 Cells(Rows.Count, …).End(xlUp).Row 패턴을 찾지 못했습니다. "
                "변경 전·후 코드 조각을 따옴표로 지정해주세요."
            )
        worksheet_variables = re.findall(
            r"\bDim\s+([A-Za-z_][A-Za-z0-9_]*)\s+As\s+(?:Excel\.)?Worksheet\b",
            code,
            re.IGNORECASE,
        )
        qualifier = worksheet_variables[0] if len(set(map(str.casefold, worksheet_variables))) == 1 else "ActiveSheet"
        column = matches[0].group(1).strip()
        replacement = (
            f"{qualifier}.Cells({qualifier}.Rows.Count, {column}).End(xlUp).Row"
        )
        return code[:matches[0].start()] + replacement + code[matches[0].end():]

    @staticmethod
    def _patch_selection_only(code):
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?UsedRange\b",
            re.IGNORECASE,
        )
        matches = list(pattern.finditer(code))
        if len(matches) != 1:
            raise AppActionBlocked(
                "선택 범위로 바꿀 단일 UsedRange 참조를 찾지 못했습니다. 변경 전·후 코드 조각을 지정해주세요."
            )
        match = matches[0]
        return code[:match.start()] + "Selection" + code[match.end():]

    @staticmethod
    def _patch_backup_sheet(code, procedure_name=None):
        variable = "jarvisOriginalSheetBackupTarget"
        if re.search(rf"\b{re.escape(variable)}\b", code, re.IGNORECASE):
            raise AppActionBlocked("JARVIS 백업 변수 이름이 이미 사용 중이라 자동 삽입하지 않았습니다.")
        procedures = parse_vba_procedures(code)
        wanted = str(procedure_name or "").casefold()
        candidates = [
            item for item in procedures if not wanted or item["name"].casefold() == wanted
        ]
        if len(candidates) != 1:
            raise AppActionBlocked("시트 백업 코드를 넣을 Sub 이름을 명령에 포함해주세요.")
        procedure = candidates[0]
        if procedure["kind"].casefold() != "sub":
            raise AppActionBlocked("원본 시트 백업 코드는 Sub 프로시저에만 삽입합니다.")
        lines = code.splitlines(keepends=True)
        index = int(procedure["line"])
        newline = "\r\n" if "\r\n" in code else "\n"
        indent_match = re.match(r"^(\s*)", lines[index - 1])
        indent = str(indent_match.group(1) if indent_match else "") + "    "
        injection = [
            f"{indent}Dim {variable} As Worksheet{newline}",
            f"{indent}Set {variable} = ActiveSheet{newline}",
            f"{indent}{variable}.Copy After:={variable}{newline}",
            f"{indent}{variable}.Activate{newline}",
        ]
        return "".join(lines[:index] + injection + lines[index:])

    def _replacement_code(self, original, params):
        if params.get("replacement_code") is not None:
            replacement = str(params["replacement_code"])
        else:
            change_kind = str(params.get("change_kind") or "").casefold()
            if change_kind == "exact_replace":
                replacement = self._exact_replacement(
                    original, params.get("find"), params.get("replace")
                )
            elif change_kind == "fix_last_row":
                replacement = self._patch_last_row(original)
            elif change_kind == "selection_only":
                replacement = self._patch_selection_only(original)
            elif change_kind == "backup_active_sheet":
                replacement = self._patch_backup_sheet(
                    original, params.get("procedure_name")
                )
            else:
                raise AppActionBlocked("지원하는 VBA 수정 방법을 안전하게 해석하지 못했습니다.")
        if len(replacement) > MAX_VBA_MODULE_CHARS:
            raise AppActionBlocked(
                f"수정된 VBA 모듈은 최대 {MAX_VBA_MODULE_CHARS:,}자까지 적용합니다."
            )
        if "\x00" in replacement:
            raise AppActionBlocked("VBA 코드에 NUL 문자가 포함되어 적용하지 않았습니다.")
        return replacement

    def _prepare_replace(self, application, params):
        workbook, base = self._workbook_context(application, params, require_write=True)
        project = self._project(workbook)
        if project is None:
            raise AppActionBlocked("현재 통합문서에 수정할 VBA 프로젝트가 없습니다.")
        _, selected = self._select_component(
            project,
            params.get("module_name"),
            procedure_name=params.get("procedure_name"),
            standard_only=True,
        )
        original = selected["code"]
        replacement = self._replacement_code(original, params)
        analysis = analyze_vba_code(replacement)
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile=selected["name"] + ":before",
            tofile=selected["name"] + ":after",
        ))
        diff = diff[:MAX_VBA_DIFF_CHARS] + ("\n…" if len(diff) > MAX_VBA_DIFF_CHARS else "")
        snapshot = {
            **base,
            "operation": "vba_replace_module",
            "project_name": str(project.Name),
            "module_name": selected["name"],
            "module_type": selected["type"],
            "original_digest": _digest(original),
            "replacement_digest": _digest(replacement),
        }
        return self._prepared(
            "vba_replace_module",
            base,
            selected["name"],
            {
                "module_name": selected["name"],
                "original_code": original,
                "replacement_code": replacement,
                "original_digest": _digest(original),
                "replacement_digest": _digest(replacement),
                "diff": diff,
                "change_kind": params.get("change_kind"),
                "analysis": analysis,
            },
            {
                "module_name": selected["name"],
                "module_type": selected["type_name"],
                "original_digest": _digest(original),
                "replacement_digest": _digest(replacement),
                "dangerous_capabilities": analysis["dangerous_capabilities"],
            },
            snapshot,
            destructive=original != replacement,
            reversible=True,
            noop=original == replacement,
            metadata={"dangerous_capabilities": analysis["dangerous_capabilities"]},
        )

    def _prepare_run(self, application, params):
        workbook, base = self._workbook_context(application, params)
        project = self._project(workbook)
        if project is None:
            raise AppActionBlocked("현재 통합문서에 실행할 VBA 프로젝트가 없습니다.")
        _, selected = self._select_component(
            project,
            params.get("module_name"),
            procedure_name=params.get("procedure_name"),
            standard_only=True,
        )
        procedures = [item for item in selected["procedures"] if item["runnable"]]
        wanted = str(params.get("procedure_name") or "").casefold()
        if wanted:
            procedures = [item for item in procedures if item["name"].casefold() == wanted]
        if len(procedures) != 1:
            names = [item["name"] for item in procedures]
            if names:
                raise AppActionAmbiguousTarget(
                    "실행할 Public Sub 이름을 지정해주세요: " + ", ".join(names),
                    candidates=names,
                    target_name="VBA procedure",
                )
            raise AppActionBlocked(
                "인수 없는 Public Sub만 실행할 수 있습니다. Private Sub, Function, 인수 있는 Sub는 지원하지 않습니다."
            )
        procedure = procedures[0]
        analysis = analyze_vba_code(selected["code"])
        snapshot = {
            **base,
            "operation": "vba_run_procedure",
            "project_name": str(project.Name),
            "module_name": selected["name"],
            "procedure_name": procedure["name"],
            "code_digest": selected["code_digest"],
        }
        return self._prepared(
            "vba_run_procedure",
            base,
            f"{selected['name']}.{procedure['name']}",
            {
                "module_name": selected["name"],
                "procedure_name": procedure["name"],
                "code_digest": selected["code_digest"],
                "analysis": analysis,
            },
            {
                "module_name": selected["name"],
                "procedure_name": procedure["name"],
                "code_digest": selected["code_digest"],
                "dangerous_capabilities": analysis["dangerous_capabilities"],
            },
            snapshot,
            destructive=True,
            reversible=False,
            metadata={"dangerous_capabilities": analysis["dangerous_capabilities"]},
        )

    def prepare(self, operation: str, params: dict) -> PreparedAction:
        if operation not in VBA_OPERATIONS:
            return super().prepare(operation, params)
        if not isinstance(params, dict):
            raise AppActionBlocked("Excel VBA 작업의 params는 객체 형식이어야 합니다.")
        with self._application() as application:
            if operation == "vba_inspect_project":
                return self._prepare_inspect(application, params)
            if operation in {"vba_read_module", "vba_analyze_module"}:
                return self._prepare_module_read(application, operation, params)
            if operation == "vba_replace_module":
                return self._prepare_replace(application, params)
            return self._prepare_run(application, params)

    @staticmethod
    def _replace_code(component, code):
        module = component.CodeModule
        count = int(getattr(module, "CountOfLines", 0) or 0)
        if count:
            module.DeleteLines(1, count)
        if code:
            module.AddFromString(code)

    def _backup_component(self, prepared, component) -> Path:
        workbook_key = hashlib.sha256(
            prepared.document_id.encode("utf-8")
        ).hexdigest()[:16]
        directory = self._vba_backup_dir / workbook_key
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", prepared.target).strip("._") or "module"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"{timestamp}-{uuid.uuid4().hex[:8]}-{safe_name}.bas"
        component.Export(str(path))
        if not path.is_file() or path.stat().st_size <= 0:
            raise AppActionVerificationError("VBA 원본 모듈 백업 파일을 확인하지 못했습니다.")
        return path

    def _current_component(self, application, prepared, *, require_write=False):
        workbook, base = self._workbook_context(
            application,
            {"document_path": prepared.document_id},
            require_write=require_write,
        )
        project = self._project(workbook)
        if project is None:
            raise AppActionContextChanged("승인 후 VBA 프로젝트가 사라졌습니다.")
        component, selected = self._select_component(
            project,
            prepared.params.get("module_name"),
            standard_only=prepared.operation in {"vba_replace_module", "vba_run_procedure"},
        )
        return workbook, base, project, component, selected

    def _execute_read(self, application, prepared):
        if prepared.operation == "vba_inspect_project":
            current = self._prepare_inspect(
                application, {"document_path": prepared.document_id}
            )
            if current.context_fingerprint != prepared.context_fingerprint:
                raise AppActionContextChanged(
                    "VBA 프로젝트 목록이 바뀌어 다시 확인해야 합니다."
                )
            state = dict(current.current_state)
            return {"success": True, "verified": True, "changed": False, **state}
        _, _, _, _, selected = self._current_component(application, prepared)
        if selected["code_digest"] != prepared.current_state["code_digest"]:
            raise AppActionContextChanged(
                "VBA 코드를 읽는 동안 모듈이 바뀌어 결과를 반환하지 않았습니다."
            )
        analysis = dict(prepared.params.get("analysis") or {})
        result = {
            "success": True,
            "verified": True,
            "changed": False,
            "module_name": prepared.params["module_name"],
            "code_digest": prepared.current_state["code_digest"],
            "procedures": prepared.current_state.get("procedures", []),
        }
        if prepared.operation == "vba_read_module":
            result["code"] = prepared.params["code"]
        else:
            result.update(analysis)
        return result

    def _execute_replace(self, application, prepared):
        _, _, _, component, selected = self._current_component(
            application, prepared, require_write=True
        )
        current = selected["code"]
        if _digest(current) != prepared.params["original_digest"]:
            raise AppActionContextChanged(
                "승인 후 VBA 모듈 코드가 바뀌어 수정안을 적용하지 않았습니다."
            )
        if prepared.noop:
            return {
                "success": True,
                "verified": True,
                "changed": False,
                "module_name": prepared.target,
                "code_digest": _digest(current),
            }
        backup_path = self._backup_component(prepared, component)
        original = prepared.params["original_code"]
        replacement = prepared.params["replacement_code"]
        try:
            self._replace_code(component, replacement)
            actual = self._component_code(component)
            if _digest(actual) != prepared.params["replacement_digest"]:
                raise AppActionVerificationError("수정된 VBA 모듈 digest가 미리보기와 다릅니다.")
        except Exception as error:
            restored = False
            try:
                self._replace_code(component, original)
                restored = _digest(self._component_code(component)) == _digest(original)
            except Exception:
                restored = False
            if isinstance(error, AppActionError) and not restored:
                raise
            raise AppActionVerificationError(
                "VBA 코드 적용에 실패해 원본 코드로 복원했습니다."
                if restored
                else f"VBA 코드 적용과 자동 복원에 실패했습니다. 백업: {backup_path}"
            ) from error
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "module_name": prepared.target,
            "code_digest": prepared.params["replacement_digest"],
            "backup_path": str(backup_path),
            "backup_sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest().upper(),
        }

    def _execute_run(self, application, prepared):
        workbook, _, _, _, selected = self._current_component(application, prepared)
        if selected["code_digest"] != prepared.params["code_digest"]:
            raise AppActionContextChanged(
                "승인 후 VBA 코드가 바뀌어 매크로를 실행하지 않았습니다."
            )
        workbook_name = str(workbook.Name).replace("'", "''")
        macro = (
            f"'{workbook_name}'!{prepared.params['module_name']}."
            f"{prepared.params['procedure_name']}"
        )
        application.Run(macro)
        return {
            "success": True,
            # ``verified`` is intentionally limited to the invocation
            # boundary.  Arbitrary business side effects require a separate,
            # user-specified post-condition and are not inferred here.
            "verified": True,
            "changed": True,
            "invocation_completed": True,
            "verification_scope": "invocation_return",
            "business_result_verified": False,
            "module_name": prepared.params["module_name"],
            "procedure_name": prepared.params["procedure_name"],
        }

    def execute(self, prepared: PreparedAction) -> dict:
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        if prepared.operation not in VBA_OPERATIONS:
            return super().execute(prepared)
        if prepared.app != "excel":
            raise AppActionBlocked("Excel VBA 작업으로 준비된 요청만 실행할 수 있습니다.")
        with self._application() as application:
            if prepared.operation in {
                "vba_inspect_project", "vba_read_module", "vba_analyze_module"
            }:
                return self._execute_read(application, prepared)
            if prepared.operation == "vba_replace_module":
                return self._execute_replace(application, prepared)
            return self._execute_run(application, prepared)

    def undo(self, prepared, record=None):
        if isinstance(prepared, dict):
            prepared = PreparedAction.from_dict(prepared)
        if prepared.operation not in VBA_OPERATIONS:
            parent = getattr(super(), "undo", None)
            if callable(parent):
                return parent(prepared, record)
            raise AppActionBlocked("이 Excel 작업은 VBA 복원 대상이 아닙니다.")
        if prepared.operation != "vba_replace_module":
            raise AppActionBlocked("VBA 코드 수정만 구조화된 원문 복원을 지원합니다.")
        with self._application() as application:
            _, _, _, component, selected = self._current_component(
                application, prepared, require_write=True
            )
            if selected["code_digest"] != prepared.params["replacement_digest"]:
                raise AppActionContextChanged(
                    "VBA 수정 뒤 코드가 다시 바뀌어 직전 원문을 복원하지 않았습니다."
                )
            self._replace_code(component, prepared.params["original_code"])
            actual = self._component_code(component)
            if _digest(actual) != prepared.params["original_digest"]:
                raise AppActionVerificationError("VBA 원문 복원 결과를 확인하지 못했습니다.")
            return {
                "success": True,
                "verified": True,
                "changed": True,
                "module_name": prepared.target,
                "code_digest": prepared.params["original_digest"],
                "restored": True,
            }
