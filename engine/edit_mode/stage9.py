"""Prototype 1.1 Stage 9 structured Excel VBA requests."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from engine.app_actions import PreparedAction
from engine.app_actions.excel_vba_adapter import VBA_OPERATIONS
from engine.edit_mode.contracts import EditPreparedAction, EditRequest, RiskLevel
from engine.edit_mode.stage5 import _quoted_values
from engine.edit_mode.stage7 import Stage7EditError, Stage7NativeEditAdapter


class Stage9EditError(Stage7EditError):
    pass


@dataclass(frozen=True)
class VbaIntent:
    operation: str
    params: dict[str, Any]
    description: str
    read_only: bool


def _path(value) -> str:
    return os.path.normcase(os.path.abspath(str(value or "")))


def _module_name(command: str) -> str:
    patterns = (
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:모듈|module)",
        r"(?:모듈|module)\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:의\s*)?(?:VBA\s*)?코드",
    )
    ignored = {"vba", "excel"}
    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if match and match.group(1).casefold() not in ignored:
            return match.group(1)
    return ""


def _procedure_name(command: str) -> str:
    patterns = (
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:Sub|Function|프로시저|매크로)",
        r"(?:Sub|Function|프로시저|매크로)\s+([A-Za-z_][A-Za-z0-9_]*)",
    )
    ignored = {"이", "현재", "vba", "엑셀", "excel"}
    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if match and match.group(1).casefold() not in ignored:
            return match.group(1)
    return ""


class StructuredVbaIntentAnalyzer:
    """Map a narrow set of Korean VBA requests to allowlisted native operations."""

    VBA_TERMS = (
        "vba", "매크로", "모듈", "프로시저", "sub", "function",
        "마지막 행", "선택한 범위만", "원본 시트", "코드",
    )

    @classmethod
    def is_vba_request(cls, text: str, context: Mapping[str, Any]) -> bool:
        if str(context.get("app_type") or "").casefold() != "excel":
            return False
        command = str(text or "").casefold()
        return any(term in command for term in cls.VBA_TERMS)

    def analyze(self, text: str, context: Mapping[str, Any]) -> VbaIntent | None:
        command = re.sub(r"\s+", " ", str(text or "")).strip()
        if not self.is_vba_request(command, context):
            return None
        common = {
            "module_name": _module_name(command),
            "procedure_name": _procedure_name(command),
        }
        lower = command.casefold()

        run_requested = any(word in lower for word in ("실행해", "실행해줘", "실행", "run"))
        if "실행 전에" in command or "실행하기 전에" in command:
            run_requested = False
        if run_requested:
            return VbaIntent(
                "vba_run_procedure",
                common,
                "Excel VBA 매크로 별도 실행",
                False,
            )

        change_words = ("고쳐", "수정", "바꿔", "교체", "처리하도록", "백업하도록")
        if any(word in command for word in change_words):
            quotes = _quoted_values(command)
            params = dict(common)
            if "마지막 행" in command:
                params["change_kind"] = "fix_last_row"
                description = "마지막 행 계산을 활성 시트 의존 없이 수정"
            elif "선택한 범위만" in command:
                params["change_kind"] = "selection_only"
                description = "UsedRange 처리를 현재 Selection으로 제한"
            elif "원본 시트" in command and "백업" in command:
                params["change_kind"] = "backup_active_sheet"
                description = "실행 시작 시 원본 활성 시트 복사 백업 추가"
            elif len(quotes) >= 2:
                params.update({
                    "change_kind": "exact_replace",
                    "find": quotes[0],
                    "replace": quotes[1],
                })
                description = "지정한 VBA 코드 조각 한 곳 교체"
            else:
                raise Stage9EditError(
                    "VBA 수정은 마지막 행 보정, 선택 범위 제한, 원본 시트 백업 또는 "
                    "변경 전·후 코드 조각 두 개를 따옴표로 지정하는 방식만 지원합니다."
                )
            return VbaIntent(
                "vba_replace_module",
                params,
                description,
                False,
            )

        if any(word in lower for word in ("목록", "있어", "포함", "감지")):
            return VbaIntent(
                "vba_inspect_project",
                common,
                "VBA 프로젝트·모듈·프로시저 목록 확인",
                True,
            )
        if any(word in lower for word in ("코드 보여", "코드 읽", "원문", "source")):
            return VbaIntent(
                "vba_read_module",
                common,
                "VBA 모듈 코드 읽기",
                True,
            )
        return VbaIntent(
            "vba_analyze_module",
            common,
            "VBA 코드 설명과 오류·위험 가능성 분석",
            True,
        )


class Stage9NativeEditAdapter(Stage7NativeEditAdapter):
    """Stage 7 editing plus Excel VBA read/edit/run with separate risk gates."""

    supported_operations = Stage7NativeEditAdapter.supported_operations | VBA_OPERATIONS

    def __init__(self, *args, vba_analyzer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.vba_analyzer = vba_analyzer or StructuredVbaIntentAnalyzer()

    @staticmethod
    def _preview(intent: VbaIntent, native: PreparedAction) -> dict:
        state = dict(native.current_state)
        description = intent.description
        dangerous = list(native.metadata.get("dangerous_capabilities") or [])
        if dangerous:
            description += " · 고위험 기능 감지: " + ", ".join(dangerous)
        if intent.operation == "vba_inspect_project":
            module_names = [item.get("name") for item in state.get("modules", [])]
            after = (
                f"VBA 프로젝트 {'있음' if state.get('has_vba_project') else '없음'} · "
                f"모듈 {state.get('module_count', 0)}개"
            )
            if module_names:
                after += " · " + ", ".join(str(item) for item in module_names)
            before = ""
        elif intent.operation == "vba_read_module":
            before = ""
            after = str(native.params.get("code") or "")
        elif intent.operation == "vba_analyze_module":
            analysis = dict(native.params.get("analysis") or {})
            before = ""
            after = str(analysis.get("explanation") or "")
            findings = analysis.get("findings") or []
            if findings:
                after += f" · 점검 항목 {len(findings)}개"
        elif intent.operation == "vba_replace_module":
            before = str(native.params.get("original_code") or "")
            after = str(native.params.get("diff") or "")
        else:
            before = "실행하지 않음"
            after = (
                f"{native.params.get('module_name')}.{native.params.get('procedure_name')} 실행"
            )
        def trim(value):
            return str(value)[:4000] + (
                "…" if len(str(value)) > 4000 else ""
            )
        return {
            "description": description,
            "before": trim(before),
            "after": trim(after),
            "target": native.target,
            "estimated_changes": native.estimated_changes,
            "noop": native.noop,
        }

    def prepare(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> EditPreparedAction:
        intent = self.vba_analyzer.analyze(request.text, context)
        if intent is None:
            return super().prepare(request, context)
        params = {
            **intent.params,
            "document_path": self.session.get("file_path"),
        }
        native = self.native_adapter.prepare(intent.operation, params)
        if not isinstance(native, PreparedAction):
            raise Stage9EditError("VBA 어댑터가 구조화된 작업을 반환하지 않았습니다.")
        if native.operation not in VBA_OPERATIONS or native.app != "excel":
            raise Stage9EditError("허용 목록 밖의 VBA 작업이 준비되어 차단했습니다.")
        if _path(native.document_id) != _path(self.session.get("file_path")):
            raise Stage9EditError("연결된 파일과 다른 Excel VBA 작업을 차단했습니다.")
        preview = self._preview(intent, native)
        dangerous = list(native.metadata.get("dangerous_capabilities") or [])
        changes_or_runs = intent.operation in {
            "vba_replace_module", "vba_run_procedure"
        } and not native.noop
        return EditPreparedAction(
            action_id=f"edit-vba-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type="excel",
            operation=intent.operation,
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": native.target,
            },
            arguments={
                "native_prepared_action": native.to_dict(),
                "preview": preview,
                "read_only": intent.read_only,
            },
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
                {"kind": "vba_context_fingerprint", "value": native.context_fingerprint},
            ),
            risk_level=RiskLevel.HIGH if changes_or_runs else RiskLevel.LOW,
            requires_approval=changes_or_runs,
            verification_plan={"method": native.verification_method},
            rollback_plan={
                "strategy": (
                    "module_backup_and_original_code_restore"
                    if intent.operation == "vba_replace_module"
                    else "none"
                )
            },
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": preview,
                "estimated_changes": native.estimated_changes,
                "native_reversible": native.reversible,
                "vba": True,
                "dangerous_capabilities": dangerous,
                "requires_reconfirmation": bool(dangerous and changes_or_runs),
            },
        )

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation not in VBA_OPERATIONS:
            return super().execute(prepared_action)
        native = PreparedAction.from_dict(
            prepared_action.arguments.get("native_prepared_action") or {}
        )
        result = self.native_adapter.execute(native)
        self._last_native_result = dict(result)
        return dict(result)

    def rollback(self, prepared_action: EditPreparedAction) -> bool:
        if prepared_action.operation not in VBA_OPERATIONS:
            return super().rollback(prepared_action)
        if self._last_native_result is not None:
            return False
        payload = prepared_action.arguments.get("native_prepared_action") or {}
        if not payload:
            return True
        native = PreparedAction.from_dict(payload)
        return bool(native.reversible)
