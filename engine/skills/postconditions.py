"""Common postcondition models and verification-result aggregation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from engine.execution_result import normalize_execution_result


PASSED = "passed"
FAILED = "failed"
NOT_AVAILABLE = "not_available"
NOT_REQUIRED = "not_required"
MANUAL_CONFIRMATION_REQUIRED = "manual_confirmation_required"

VERIFICATION_STATUSES = frozenset({
    PASSED,
    FAILED,
    NOT_AVAILABLE,
    NOT_REQUIRED,
    MANUAL_CONFIRMATION_REQUIRED,
})

SUPPORTED_POSTCONDITIONS = frozenset({
    "window_exists",
    "control_exists",
    "file_exists",
    "file_created",
    "cell_equals",
    "cell_format_matches",
    "text_contains",
})

_STATUS_ALIASES = {
    "verified": PASSED,
    "success": PASSED,
    "passed": PASSED,
    "failed": FAILED,
    "verification_failed": FAILED,
    "not_available": NOT_AVAILABLE,
    "not_required": NOT_REQUIRED,
    "confirmation_required": MANUAL_CONFIRMATION_REQUIRED,
    "manual_confirmation_required": MANUAL_CONFIRMATION_REQUIRED,
}

_PLACEHOLDER_RE = re.compile(r"\{([0-9a-zA-Z가-힣_]+)\}")
_CURRENT_DOCUMENT_NAMES = frozenset({"", "현재 문서", "current document"})


class InvalidPostcondition(ValueError):
    """A persisted postcondition does not use the supported safe schema."""


class PostconditionNotAvailable(RuntimeError):
    """The current runtime cannot inspect the requested postcondition."""


@dataclass(frozen=True)
class Postcondition:
    """Serializable, privacy-bounded description of one expected final state."""

    type: str
    target: Any
    expected: Any = None
    options: dict[str, Any] | None = None

    @classmethod
    def from_value(cls, value):
        if not isinstance(value, dict):
            raise InvalidPostcondition("사후조건은 객체 형식이어야 합니다.")
        condition_type = str(value.get("type") or "").strip().casefold()
        if condition_type not in SUPPORTED_POSTCONDITIONS:
            raise InvalidPostcondition(
                f"지원하지 않는 사후조건입니다: {condition_type or '없음'}"
            )
        if "target" not in value:
            raise InvalidPostcondition("사후조건 대상이 없습니다.")
        options = value.get("options", {})
        if not isinstance(options, dict):
            raise InvalidPostcondition("사후조건 options는 객체여야 합니다.")
        return cls(
            type=condition_type,
            target=value.get("target"),
            expected=value.get("expected"),
            options=dict(options),
        )

    def to_dict(self):
        result = {"type": self.type, "target": self.target}
        if self.expected is not None:
            result["expected"] = self.expected
        if self.options:
            result["options"] = dict(self.options)
        return result


def normalize_postconditions(values, *, limit=20):
    """Keep only supported structured conditions before persisting a skill.

    Invalid or invented condition types are deliberately discarded instead of
    being saved as trusted success criteria.
    """

    if not isinstance(values, (list, tuple)):
        return []
    normalized = []
    for value in values:
        try:
            normalized.append(Postcondition.from_value(value).to_dict())
        except InvalidPostcondition:
            continue
        if len(normalized) >= max(0, int(limit)):
            break
    return normalized


@dataclass(frozen=True)
class PostconditionSummary:
    result: dict
    process_success: bool
    verified: bool
    verification_status: str
    checks: tuple[dict, ...]


class PostconditionEvaluator:
    """Evaluate explicit conditions or normalize existing executor evidence."""

    def __init__(
        self,
        action_executor=None,
        checkers: dict[str, Callable[[Postcondition, dict], Any]] | None = None,
    ):
        self.action_executor = action_executor
        self.checkers = dict(checkers or {})

    def evaluate(
        self,
        value,
        *,
        route,
        conditions=None,
        slots=None,
        verification_required=False,
    ):
        result = normalize_execution_result(value, action=route)
        process_success = bool(result.get("success"))
        context = {
            "result": result,
            "route": str(route or ""),
            "slots": dict(slots or {}),
        }

        raw_conditions = conditions if isinstance(conditions, (list, tuple)) else []
        if raw_conditions:
            checks = tuple(
                self._evaluate_raw_condition(item, context)
                for item in raw_conditions
            )
            verification_status = self._aggregate(checks)
        else:
            checks = self._existing_checks(result)
            if checks:
                verification_status = self._aggregate(checks)
            else:
                verification_status = self._status_without_checks(
                    result,
                    route=route,
                    verification_required=verification_required,
                )

        if not process_success:
            verification_status = FAILED
        verified = process_success and verification_status == PASSED

        result["process_success"] = process_success
        result["verified_success"] = verified
        result["verified"] = verified
        result["verification_status"] = verification_status
        result["verification"] = [dict(item) for item in checks]
        data = dict(result.get("data") or {})
        data["verification"] = [dict(item) for item in checks]
        data["postcondition_result"] = {
            "process_success": process_success,
            "verified": verified,
            "verification_status": verification_status,
            "checks": [dict(item) for item in checks],
        }
        result["data"] = data

        if verification_status == FAILED:
            result["success"] = False
            result["status"] = "verification_failed"
            result["error_type"] = "verification_error"
            if process_success:
                result["message"] = (
                    "실행 프로세스는 종료됐지만 요청한 결과의 사후조건 검증에 실패했습니다."
                )
                result["response"] = result["message"]

        return PostconditionSummary(
            result=result,
            process_success=process_success,
            verified=verified,
            verification_status=verification_status,
            checks=checks,
        )

    def _evaluate_raw_condition(self, value, context):
        try:
            condition = Postcondition.from_value(value)
        except InvalidPostcondition as error:
            return self._check_result(
                "invalid", NOT_AVAILABLE, reason=str(error)
            )

        try:
            checker = self.checkers.get(condition.type)
            if checker is not None:
                outcome = checker(condition, dict(context))
            else:
                outcome = getattr(self, f"_check_{condition.type}")(
                    condition, context
                )
            return self._normalize_checker_outcome(condition.type, outcome)
        except PostconditionNotAvailable as error:
            return self._check_result(
                condition.type, NOT_AVAILABLE, reason=str(error)
            )
        except Exception as error:  # A checker error is not proof of failure.
            return self._check_result(
                condition.type,
                NOT_AVAILABLE,
                reason=f"검증기를 사용할 수 없습니다: {error}",
            )

    @staticmethod
    def _check_result(condition_type, status, **extra):
        normalized = _STATUS_ALIASES.get(str(status or "").casefold(), NOT_AVAILABLE)
        result = {
            "type": condition_type,
            "status": normalized,
            "passed": normalized == PASSED,
        }
        result.update({key: value for key, value in extra.items() if value is not None})
        return result

    def _normalize_checker_outcome(self, condition_type, outcome):
        if isinstance(outcome, bool):
            return self._check_result(
                condition_type, PASSED if outcome else FAILED
            )
        if isinstance(outcome, str):
            return self._check_result(condition_type, outcome)
        if not isinstance(outcome, dict):
            return self._check_result(
                condition_type,
                NOT_AVAILABLE,
                reason="검증기가 판정 결과를 반환하지 않았습니다.",
            )
        raw = dict(outcome)
        if "status" in raw:
            status = raw.pop("status")
        elif "passed" in raw:
            status = PASSED if raw.get("passed") else FAILED
        else:
            status = NOT_AVAILABLE
        raw.pop("passed", None)
        return self._check_result(condition_type, status, **raw)

    @classmethod
    def _canonical_status(cls, value, *, passed=None):
        if passed is True:
            return PASSED
        if passed is False:
            return FAILED
        return _STATUS_ALIASES.get(str(value or "").strip().casefold(), NOT_AVAILABLE)

    def _existing_checks(self, result):
        raw_checks = result.get("verification", [])
        if not isinstance(raw_checks, list):
            raw_checks = (result.get("data") or {}).get("verification", [])
        checks = []
        for item in raw_checks if isinstance(raw_checks, list) else []:
            if not isinstance(item, dict):
                continue
            source = dict(item)
            source_status = source.get("status")
            passed = source.get("passed") if "passed" in source else None
            status = self._canonical_status(source_status, passed=passed)
            source["source_status"] = source_status
            source["status"] = status
            source["passed"] = status == PASSED
            source.setdefault(
                "type", str(source.get("action") or "executor_verification")
            )
            checks.append(source)
        return tuple(checks)

    @staticmethod
    def _aggregate(checks):
        statuses = [item.get("status") for item in checks]
        if FAILED in statuses:
            return FAILED
        if MANUAL_CONFIRMATION_REQUIRED in statuses:
            return MANUAL_CONFIRMATION_REQUIRED
        if NOT_AVAILABLE in statuses:
            return NOT_AVAILABLE
        if statuses and all(status == PASSED for status in statuses):
            return PASSED
        if statuses and all(status == NOT_REQUIRED for status in statuses):
            return NOT_REQUIRED
        return NOT_AVAILABLE

    def _status_without_checks(self, result, *, route, verification_required):
        if result.get("verified") is True:
            return PASSED
        status = self._canonical_status(result.get("verification_status"))
        if status != NOT_AVAILABLE:
            return status
        # UIA and learned dynamic Python have no trustworthy implicit final
        # state. They require a user check unless an explicit condition exists.
        if str(route or "") in {"uia", "python"} or verification_required:
            return MANUAL_CONFIRMATION_REQUIRED
        return NOT_REQUIRED

    @staticmethod
    def _resolve(value, context):
        slots = context["slots"]
        if isinstance(value, dict):
            source = str(value.get("source") or "").casefold()
            if source == "slot":
                name = str(value.get("name") or "")
                if name not in slots:
                    raise PostconditionNotAvailable(
                        f"사후조건 슬롯 '{name}' 값이 없습니다."
                    )
                return slots[name]
            if source == "literal":
                return value.get("value")
            if source == "result":
                return PostconditionEvaluator._nested_value(
                    context["result"], value.get("path") or value.get("name")
                )
            return {
                key: PostconditionEvaluator._resolve(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [PostconditionEvaluator._resolve(item, context) for item in value]
        if isinstance(value, str):
            def replace(match):
                name = match.group(1)
                if name not in slots:
                    raise PostconditionNotAvailable(
                        f"사후조건 슬롯 '{name}' 값이 없습니다."
                    )
                return str(slots[name])

            return _PLACEHOLDER_RE.sub(replace, value)
        return value

    @staticmethod
    def _nested_value(value, path):
        current = value
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                current = current[int(part)]
            else:
                raise PostconditionNotAvailable(
                    f"실행 결과에서 '{path}' 값을 읽을 수 없습니다."
                )
        return current

    @staticmethod
    def _target_item(target, *names):
        if not isinstance(target, dict):
            return target
        for name in names:
            if name in target:
                return target[name]
        return None

    def _check_file_exists(self, condition, context):
        target = self._resolve(condition.target, context)
        path = self._target_item(target, "path", "file", "value")
        if not str(path or "").strip():
            raise PostconditionNotAvailable("검증할 파일 경로가 없습니다.")
        expanded = os.path.abspath(
            os.path.expandvars(os.path.expanduser(str(path)))
        )
        exists = os.path.isfile(expanded)
        return {
            "status": PASSED if exists else FAILED,
            "target": expanded,
            "actual": exists,
        }

    def _check_file_created(self, condition, context):
        return self._check_file_exists(condition, context)

    def _uia(self):
        uia = getattr(self.action_executor, "ui_automation", None)
        if uia is None:
            raise PostconditionNotAvailable("UI Automation 검증기가 연결되지 않았습니다.")
        return uia

    def _check_window_exists(self, condition, context):
        target = self._resolve(condition.target, context)
        app_name = self._target_item(target, "app", "window", "name", "value")
        if not str(app_name or "").strip():
            raise PostconditionNotAvailable("검증할 창 이름이 없습니다.")
        try:
            window = self._uia().find_window(str(app_name))
        except Exception as error:
            return {"status": FAILED, "reason": str(error)}
        return {
            "status": PASSED,
            "matched_name": str(window.window_text() or ""),
        }

    def _check_control_exists(self, condition, context):
        target = self._resolve(condition.target, context)
        if not isinstance(target, dict):
            raise PostconditionNotAvailable("컨트롤 대상 구조가 없습니다.")
        app_name = self._target_item(target, "app", "window")
        control_name = self._target_item(target, "control", "name")
        if not str(app_name or "").strip() or not str(control_name or "").strip():
            raise PostconditionNotAvailable("창 또는 컨트롤 이름이 없습니다.")
        try:
            uia = self._uia()
            window = uia.find_window(str(app_name))
            control = uia._find_control(  # Stage 9 replaces this locator ladder.
                window,
                str(control_name),
                editable=bool(target.get("editable", False)),
            )
        except Exception as error:
            return {"status": FAILED, "reason": str(error)}
        return {
            "status": PASSED,
            "matched_name": str(control.window_text() or ""),
        }

    @staticmethod
    def _iter_dicts(value, depth=0):
        if depth > 8:
            return
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from PostconditionEvaluator._iter_dicts(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from PostconditionEvaluator._iter_dicts(item, depth + 1)

    def _excel_observation(self, target, context):
        workbook = str(target.get("workbook") or "").strip().casefold()
        sheet = str(target.get("sheet") or "").strip().casefold()
        cell = str(target.get("cell") or target.get("range") or "").strip().casefold()
        for item in self._iter_dicts(context["result"]):
            if str(item.get("app") or "").casefold() != "excel":
                continue
            actual_workbook = str(item.get("workbook_name") or "").strip().casefold()
            actual_sheet = str(item.get("sheet") or "").strip().casefold()
            actual_cell = str(item.get("target") or "").strip().casefold()
            if workbook not in _CURRENT_DOCUMENT_NAMES and workbook != actual_workbook:
                continue
            if sheet and sheet != actual_sheet:
                continue
            if cell and cell != actual_cell:
                continue
            return item
        raise PostconditionNotAvailable(
            "기존 Excel 어댑터의 재조회 검증 결과를 찾을 수 없습니다."
        )

    def _check_cell_equals(self, condition, context):
        target = self._resolve(condition.target, context)
        if not isinstance(target, dict):
            raise PostconditionNotAvailable("Excel 셀 대상 구조가 없습니다.")
        expected = self._resolve(condition.expected, context)
        observation = self._excel_observation(target, context)
        after = observation.get("after")
        if isinstance(after, dict):
            if isinstance(expected, str) and expected.startswith("="):
                actual = after.get("current_formula", after.get("formula"))
            else:
                actual = after.get(
                    "current_value", after.get("value", after.get("current_formula"))
                )
        else:
            actual = after
        passed = actual == expected
        return {
            "status": PASSED if passed else FAILED,
            "expected": expected,
            "actual": actual,
            "verification_method": observation.get("verification_method"),
        }

    @staticmethod
    def _contains_subset(actual, expected):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                key in actual and PostconditionEvaluator._contains_subset(
                    actual[key], value
                )
                for key, value in expected.items()
            )
        if isinstance(expected, list):
            return isinstance(actual, list) and len(actual) == len(expected) and all(
                PostconditionEvaluator._contains_subset(current, wanted)
                for current, wanted in zip(actual, expected)
            )
        return actual == expected

    def _check_cell_format_matches(self, condition, context):
        target = self._resolve(condition.target, context)
        expected = self._resolve(condition.expected, context)
        if not isinstance(target, dict) or not isinstance(expected, dict):
            raise PostconditionNotAvailable("Excel 셀 서식 대상 또는 기대값이 없습니다.")
        observation = self._excel_observation(target, context)
        after = observation.get("after")
        comparable = after.get("desired", after) if isinstance(after, dict) else after
        passed = self._contains_subset(comparable, expected)
        return {
            "status": PASSED if passed else FAILED,
            "expected": expected,
            "actual": comparable,
            "verification_method": observation.get("verification_method"),
        }

    def _check_text_contains(self, condition, context):
        target = self._resolve(condition.target, context)
        expected = self._resolve(condition.expected, context)
        if expected is None:
            raise PostconditionNotAvailable("포함 여부를 확인할 기대 문자열이 없습니다.")
        if isinstance(target, dict) and "path" in target:
            path = os.path.abspath(
                os.path.expandvars(os.path.expanduser(str(target["path"])))
            )
            try:
                with open(path, "r", encoding=str(target.get("encoding") or "utf-8")) as stream:
                    actual = stream.read()
            except OSError as error:
                return {"status": FAILED, "reason": str(error), "target": path}
        elif isinstance(target, dict):
            actual = self._target_item(target, "text", "value")
        else:
            actual = target
        if actual is None:
            raise PostconditionNotAvailable("검증할 텍스트를 읽을 수 없습니다.")
        passed = str(expected) in str(actual)
        return {
            "status": PASSED if passed else FAILED,
            "expected": str(expected),
        }
