"""Stage 12 anonymized failure incidents and read-only remediation plans."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import threading
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json
from engine.version import runtime_info


INCIDENT_SCHEMA_VERSION = 1
DEFAULT_INCIDENT_PATH = user_data_path("diagnostic_incidents.json")
INCIDENT_STATUSES = frozenset({"new", "acknowledged", "resolved", "dismissed"})
IGNORED_EXECUTION_STATUSES = frozenset({
    "success", "cancelled", "confirmation_required", "busy"
})
PATH_RE = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\)[^\r\n\t<>|]+|/(?:home|users|tmp|var|opt)/[^\s]+"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s]+")
LONG_ID_RE = re.compile(r"\b[a-fA-F0-9]{16,}\b")
QUOTED_RE = re.compile(r'(?s)(["“\'])(?:(?!\1).){5,}?\1')

SAFE_EVENT_DETAIL_KEYS = frozenset({
    "step", "route", "operation", "error_type", "failed_step",
    "verification_status", "fallback_level", "locator_strategy",
    "selected_route", "status", "retryable", "rollback_performed",
    "rollback_success",
})

CATEGORY_GUIDANCE = {
    "workflow_step_failure": {
        "headline": "문서 워크플로의 특정 단계에서 실행이 중단되었습니다.",
        "causes": [
            "해당 Office 앱이 일시적으로 응답하지 않았을 수 있습니다.",
            "산출물 경로 또는 파일 상태가 단계 실행 중 바뀌었을 수 있습니다.",
            "단계별 네이티브 어댑터의 재현 가능한 예외일 수 있습니다.",
        ],
        "fix": "저장된 성공 단계는 유지하고 실패 단계의 소유 fixture 재현 테스트를 먼저 추가합니다.",
        "tests": [
            "실패 단계 단독 재현 테스트",
            "성공 단계 비중복 재개 테스트",
            "산출물 지문과 Office 프로세스 정리 테스트",
        ],
        "confidence": 0.9,
    },
    "verification_mismatch": {
        "headline": "실행은 끝났지만 실제 결과가 기대 상태와 일치하지 않았습니다.",
        "causes": [
            "앱이 변경을 거부하거나 일부만 적용했을 수 있습니다.",
            "선택 대상이 실행 직전에 바뀌었을 수 있습니다.",
            "재조회 검증기가 앱의 최신 상태를 읽지 못했을 수 있습니다.",
        ],
        "fix": "실패한 사후조건을 fixture로 고정하고 실행과 검증 사이의 상태 변화를 분리해 테스트합니다.",
        "tests": ["실패 사후조건 재현", "롤백 성공·실패 분리", "재조회 지연 경계"],
        "confidence": 0.9,
    },
    "context_changed": {
        "headline": "준비 이후 문서 또는 선택 문맥이 바뀌어 안전하게 차단했습니다.",
        "causes": [
            "사용자가 다른 문서나 선택 영역으로 이동했습니다.",
            "연결 문서가 닫히거나 같은 이름의 다른 파일로 바뀌었습니다.",
        ],
        "fix": "오류 수정 대상이 아니라 안전 차단일 가능성이 높습니다. 다시 연결한 뒤 새 문맥으로 재시도합니다.",
        "tests": ["문서 전환 차단", "선택 fingerprint 변경", "닫힌 문서 후속 명령 차단"],
        "confidence": 0.95,
    },
    "permission_or_security": {
        "headline": "운영체제·Office 보안 또는 문서 보호 정책이 작업을 차단했습니다.",
        "causes": [
            "읽기 전용 또는 보호 문서일 수 있습니다.",
            "Office 보안 센터의 프로그램 방식 접근 설정이 꺼져 있을 수 있습니다.",
            "대상 폴더 쓰기 권한이 없을 수 있습니다.",
        ],
        "fix": "보안 설정을 자동 변경하지 말고 현재 차단 조건을 사용자에게 설명하는 회귀 테스트를 추가합니다.",
        "tests": ["읽기 전용 문서", "보호 문서", "쓰기 권한 없음", "보안 센터 차단"],
        "confidence": 0.85,
    },
    "office_busy": {
        "headline": "Office COM 호출이 일시적인 busy 또는 호출 거부 상태였습니다.",
        "causes": [
            "Office 대화상자나 편집 작업이 호출을 잠시 막았습니다.",
            "RPC 호출 거부 또는 서버 재시도 응답이 발생했습니다.",
        ],
        "fix": "부작용 없는 조회에만 제한 재시도를 적용하고 쓰기 작업은 중복 실행되지 않는지 검증합니다.",
        "tests": ["RPC_E_CALL_REJECTED", "SERVERCALL_RETRYLATER", "쓰기 비중복"],
        "confidence": 0.85,
    },
    "timeout": {
        "headline": "작업이 제한 시간 안에 완료되지 않았습니다.",
        "causes": ["외부 앱이 응답하지 않았습니다.", "대상 데이터가 현재 안전 한도를 넘었을 수 있습니다."],
        "fix": "취소 가능한 최소 재현을 만들고 종료 후 자식 프로세스와 임시 파일 정리를 검증합니다.",
        "tests": ["시간 제한", "사용자 취소", "프로세스·임시 파일 정리"],
        "confidence": 0.8,
    },
    "dependency_missing": {
        "headline": "필수 런타임 구성요소 또는 앱 자동화 인터페이스를 찾지 못했습니다.",
        "causes": ["필수 Python 모듈이 누락되었습니다.", "대상 Office/HWP 앱이 설치되지 않았거나 등록되지 않았습니다."],
        "fix": "startup preflight와 패키징 hidden import를 확인하고 설치 환경 fixture를 추가합니다.",
        "tests": ["startup_check", "frozen import", "앱 미설치 안내"],
        "confidence": 0.85,
    },
    "target_unavailable": {
        "headline": "명령 대상 문서·앱·UI 요소를 찾지 못했습니다.",
        "causes": ["대상이 닫혔거나 이름이 바뀌었습니다.", "여러 후보가 있어 안전하게 특정할 수 없었습니다."],
        "fix": "정확한 식별값을 포함한 소유 fixture를 만들고 모호한 대상이 자동 선택되지 않는지 검증합니다.",
        "tests": ["대상 없음", "동명이인 대상", "다른 활성 문서 차단"],
        "confidence": 0.8,
    },
    "validation_block": {
        "headline": "입력 또는 안전 정책 검증 단계에서 실행을 차단했습니다.",
        "causes": ["허용 목록 밖의 작업일 수 있습니다.", "입력 범위나 값이 안전 한도를 넘었을 수 있습니다."],
        "fix": "지원 범위인지 먼저 판단하고, 지원 대상이면 실패 입력을 단위 테스트로 추가합니다.",
        "tests": ["허용 목록 경계", "값·크기 제한", "승인 없는 고위험 작업 차단"],
        "confidence": 0.75,
    },
    "execution_exception": {
        "headline": "실행 경계 안에서 분류되지 않은 예외가 발생했습니다.",
        "causes": ["앱별 어댑터 예외 처리가 부족할 수 있습니다.", "환경별 API 응답 형태가 예상과 다를 수 있습니다."],
        "fix": "익명화된 구조 정보로 소유 fixture를 만들고 예외를 구체적 오류 유형으로 좁힙니다.",
        "tests": ["동일 구조 실패 재현", "예외 유형 보존", "실패 후 다음 명령 복구"],
        "confidence": 0.55,
    },
}


class DiagnosticIncidentError(ValueError):
    pass


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hash(value) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest().upper()


def _identifier(value, limit=100) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(rf"[a-zA-Z0-9_.:-]{{1,{int(limit)}}}", raw):
        return raw
    # Natural-language labels can contain command or document content. They
    # remain correlatable without retaining any recoverable fragment.
    return f"anon_{_hash(raw)[:12].casefold()}" if raw else "unknown"


def redact_sensitive_text(value) -> str:
    """Return a bounded template; raw text is never persisted in incidents."""
    text = str(value or "")[:4_000]
    text = URL_RE.sub("<URL>", text)
    text = EMAIL_RE.sub("<EMAIL>", text)
    text = PATH_RE.sub("<PATH>", text)
    text = QUOTED_RE.sub("<VALUE>", text)
    text = LONG_ID_RE.sub("<ID>", text)
    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if username and len(username) >= 2:
        text = re.sub(re.escape(username), "<USER>", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    # Error templates are useful only as short signatures. Long natural text
    # could be document content, so retain no more than one compact sentence.
    return text[:240]


def _file_version(path) -> str | None:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return ".".join(str(item) for item in (
            ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF
        ))
    except Exception:
        return None


def _registered_app_version(executable: str) -> dict[str, Any]:
    result = {"installed": False, "version": None}
    if os.name != "nt":
        return result
    try:
        import winreg
    except ImportError:
        return result
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    views = [0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)]
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                    path = str(winreg.QueryValue(key, None) or "").strip('"')
            except OSError:
                continue
            result["installed"] = bool(path)
            if path:
                result["version"] = _file_version(path)
            return result
    return result


def collect_environment_snapshot() -> dict[str, Any]:
    """Collect versions without starting or attaching to user applications."""
    identity = runtime_info()
    return {
        "app_version": identity.get("app_version"),
        "git_commit": identity.get("git_commit"),
        "build_kind": identity.get("build_kind"),
        "python_version": identity.get("python_version"),
        "os_family": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "applications": {
            "excel": _registered_app_version("EXCEL.EXE"),
            "word": _registered_app_version("WINWORD.EXE"),
            "powerpoint": _registered_app_version("POWERPNT.EXE"),
            "hwp": _registered_app_version("Hwp.exe"),
        },
    }


def _safe_environment(value) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    safe = {}
    for key in (
        "app_version", "git_commit", "build_kind", "python_version",
        "os_family", "os_release", "architecture",
    ):
        safe[key] = _identifier(raw.get(key)) if raw.get(key) else None
    applications = (
        raw.get("applications")
        if isinstance(raw.get("applications"), Mapping)
        else {}
    )
    safe["applications"] = {}
    for name in ("excel", "word", "powerpoint", "hwp"):
        app = (
            applications.get(name)
            if isinstance(applications.get(name), Mapping)
            else {}
        )
        safe["applications"][name] = {
            "installed": bool(app.get("installed", False)),
            "version": _identifier(app.get("version")) if app.get("version") else None,
        }
    return safe


def _safe_event(event) -> dict[str, Any] | None:
    if not isinstance(event, Mapping):
        return None
    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    safe_details = {}
    for key in SAFE_EVENT_DETAIL_KEYS:
        if key not in details:
            continue
        value = details[key]
        if isinstance(value, bool) or value is None:
            safe_details[key] = value
        elif isinstance(value, (int, float)):
            safe_details[key] = value
        else:
            safe_details[key] = _identifier(value)
    return {
        "action": _identifier(event.get("action")),
        "status": _identifier(event.get("status")),
        "details": safe_details,
    }


def _category(record: Mapping[str, Any]) -> str:
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    error_type = str(record.get("error_type") or result.get("error_type") or "").casefold()
    status = str(record.get("status") or result.get("status") or "").casefold()
    failed_step = record.get("failed_step", result.get("failed_step"))
    text = " ".join((
        str(record.get("error") or ""),
        str(record.get("response") or ""),
        str(result.get("message") or ""),
    )).casefold()
    if failed_step not in (None, "", -1):
        return "workflow_step_failure"
    if error_type == "verification_error" or "verification" in status:
        return "verification_mismatch"
    if status in {"context_changed", "stale_context"} or "fingerprint" in text:
        return "context_changed"
    if error_type == "timeout" or "timed out" in text or "시간" in text and "초과" in text:
        return "timeout"
    if any(term in text for term in (
        "access is denied", "permission", "read-only", "readonly", "protected",
        "trust center", "programmatic access", "보안 센터", "읽기 전용", "보호"
    )):
        return "permission_or_security"
    if any(term in text for term in (
        "rpc_e_call_rejected", "servercall_retrylater", "call was rejected",
        "busy", "com_error", "호출이 거부"
    )):
        return "office_busy"
    if any(term in text for term in (
        "modulenotfounderror", "importerror", "no module named", "class not registered",
        "모듈을 찾", "설치되지"
    )):
        return "dependency_missing"
    if error_type == "target_not_found":
        return "target_unavailable"
    if error_type == "validation_error" or status == "blocked":
        return "validation_block"
    return "execution_exception"


def _structural_result(record: Mapping[str, Any]) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    operation = data.get("operation")
    app_type = data.get("app_type")
    document_name = str(data.get("document_name") or "")
    extension = Path(document_name).suffix.casefold() if document_name else ""
    target = result.get("target")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    safe = {
        "action": _identifier(result.get("action") or "command"),
        "operation": _identifier(operation) if operation else None,
        "app_type": _identifier(app_type) if app_type else None,
        "document_extension": extension if re.fullmatch(r"\.[a-z0-9]{1,8}", extension) else None,
        "target_hash": _hash(target) if target else None,
        "mode": _identifier(metadata.get("mode")) if metadata.get("mode") else None,
        "edit_session_hash": _hash(metadata.get("edit_session_id")) if metadata.get("edit_session_id") else None,
        "document_fingerprint": (
            _hash(metadata.get("document_fingerprint"))
            if metadata.get("document_fingerprint") else None
        ),
        "status": _identifier(record.get("status") or result.get("status")),
        "error_type": _identifier(record.get("error_type") or result.get("error_type")),
        "failed_step": (
            (
                int(record.get("failed_step", result.get("failed_step")))
                if isinstance(
                    record.get("failed_step", result.get("failed_step")), int
                )
                else _identifier(record.get("failed_step", result.get("failed_step")))
            )
            if record.get("failed_step", result.get("failed_step")) not in (None, "")
            else None
        ),
        "retryable": bool(record.get("retryable", result.get("retryable", False))),
        "verified": bool(record.get("verified", result.get("verified", False))),
    }
    return safe


def privacy_safe_execution_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Remove command, path, target, response, and document content from a log."""
    if not isinstance(record, Mapping):
        return {}
    if record.get("privacy_safe") is True:
        return copy.deepcopy(dict(record))
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    label = str(record.get("label") or "")
    safe = {}
    safe["privacy_safe"] = True
    for key in (
        "execution_id", "app_version", "git_commit", "build_kind",
        "started_at", "paused_at", "resumed_at", "finished_at", "duration_ms",
        "success", "verified", "retryable",
        "diagnostic_incident_id",
    ):
        value = record.get(key)
        if value is not None:
            safe[key] = value
    if record.get("status") is not None:
        safe["status"] = _identifier(record.get("status"))
    if record.get("error_type") is not None:
        safe["error_type"] = _identifier(record.get("error_type"))
    if record.get("failed_step") not in (None, ""):
        safe["failed_step"] = (
            int(record["failed_step"])
            if isinstance(record["failed_step"], int)
            else _identifier(record["failed_step"])
        )
    safe["command_hash"] = str(record.get("command_hash") or _hash(label))
    safe["command_length"] = int(record.get("command_length") or len(label))
    safe["metadata"] = {
        "mode": _identifier(metadata.get("mode")) if metadata.get("mode") else None,
        "use_api": bool(metadata.get("use_api", False)),
        "session_hash": _hash(metadata.get("session_id")) if metadata.get("session_id") else None,
        "edit_session_hash": _hash(metadata.get("edit_session_id")) if metadata.get("edit_session_id") else None,
        "document_fingerprint_hash": (
            _hash(metadata.get("document_fingerprint"))
            if metadata.get("document_fingerprint") else None
        ),
    }
    safe["events"] = [
        item for item in (
            _safe_event(event) for event in list(record.get("events") or [])[-30:]
        ) if item is not None
    ]
    if record.get("response"):
        safe["response_signature"] = _hash(record.get("response"))
    if record.get("error"):
        safe["error_signature"] = _hash(record.get("error"))
    result = record.get("result") if isinstance(record.get("result"), Mapping) else None
    if result is not None:
        structure = _structural_result(record)
        safe["result"] = {
            "success": bool(result.get("success")),
            "action": structure.get("action"),
            "operation": structure.get("operation"),
            "app_type": structure.get("app_type"),
            "document_extension": structure.get("document_extension"),
            "status": structure.get("status"),
            "error_type": structure.get("error_type"),
            "failed_step": structure.get("failed_step"),
            "retryable": structure.get("retryable"),
            "verified": structure.get("verified"),
        }
    return safe


class DiagnosticIncidentManager:
    """Group failures, remove content, and create read-only fix/test guidance."""

    def __init__(
        self,
        path=None,
        *,
        max_incidents=200,
        environment_provider=None,
    ):
        self.path = str(path or DEFAULT_INCIDENT_PATH)
        self.max_incidents = max(10, min(int(max_incidents), 2_000))
        self.environment_provider = environment_provider or collect_environment_snapshot
        self._lock = threading.RLock()
        loaded = safe_read_json(self.path, self._default_data())
        self._data = self._normalize(loaded)

    @staticmethod
    def _default_data():
        return {"schema_version": INCIDENT_SCHEMA_VERSION, "incidents": []}

    @staticmethod
    def _normalize(value):
        raw = value if isinstance(value, Mapping) else {}
        incidents = []
        for item in raw.get("incidents", []) if isinstance(raw.get("incidents"), list) else []:
            if not isinstance(item, Mapping):
                continue
            identifier = str(item.get("incident_id") or "")
            fingerprint = str(item.get("fingerprint") or "")
            status = str(item.get("status") or "new")
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                continue
            if not re.fullmatch(r"[A-F0-9]{64}", fingerprint):
                continue
            if status not in INCIDENT_STATUSES:
                status = "new"
            normalized = copy.deepcopy(dict(item))
            normalized["status"] = status
            try:
                occurrence_count = int(item.get("occurrence_count") or 1)
            except (TypeError, ValueError):
                occurrence_count = 1
            normalized["occurrence_count"] = max(1, occurrence_count)
            normalized["raw_content_stored"] = False
            normalized["automatic_code_change"] = False
            incidents.append(normalized)
        return {"schema_version": INCIDENT_SCHEMA_VERSION, "incidents": incidents}

    def _save_locked(self):
        atomic_write_json(self.path, self._data, max_versions=3)

    @staticmethod
    def _fingerprint(structure, category, events) -> str:
        signature = {
            "category": category,
            "action": structure.get("action"),
            "operation": structure.get("operation"),
            "app_type": structure.get("app_type"),
            "document_extension": structure.get("document_extension"),
            "failed_step": structure.get("failed_step"),
            "error_type": structure.get("error_type"),
            "events": [(item["action"], item["status"]) for item in events],
        }
        return _hash(json.dumps(signature, sort_keys=True, separators=(",", ":")))

    def record_execution_failure(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        if not isinstance(record, Mapping) or bool(record.get("success")):
            return None
        status = str(record.get("status") or "").casefold()
        if status in IGNORED_EXECUTION_STATUSES:
            return None
        result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
        if str(result.get("action") or "").casefold() == "self_diagnosis":
            return None
        structure = _structural_result(record)
        category = _category(record)
        events = [
            safe for safe in (_safe_event(item) for item in list(record.get("events") or [])[-30:])
            if safe is not None
        ]
        fingerprint = self._fingerprint(structure, category, events)
        guidance = CATEGORY_GUIDANCE[category]
        now = _timestamp()
        with self._lock:
            existing = next((
                item for item in self._data["incidents"]
                if item.get("fingerprint") == fingerprint
            ), None)
            if existing is not None:
                existing["occurrence_count"] = int(existing.get("occurrence_count") or 1) + 1
                existing["last_seen_at"] = now
                existing["last_execution_hash"] = _hash(record.get("execution_id"))
                existing["status"] = "new" if existing.get("status") == "resolved" else existing.get("status", "new")
                self._save_locked()
                return copy.deepcopy(existing)
            environment = _safe_environment(self.environment_provider())
            error_template = _identifier(
                f"{category}:{structure.get('error_type') or 'unknown'}"
            )
            incident = {
                "schema_version": INCIDENT_SCHEMA_VERSION,
                "incident_id": uuid.uuid4().hex,
                "fingerprint": fingerprint,
                "status": "new",
                "occurrence_count": 1,
                "first_seen_at": now,
                "last_seen_at": now,
                "last_execution_hash": _hash(record.get("execution_id")),
                "command": {
                    "input_hash": _hash(record.get("label")),
                    "input_length": len(str(record.get("label") or "")),
                    "mode": structure.get("mode"),
                },
                "interpreted": structure,
                "expected": {
                    "status": "success",
                    "verified": True,
                },
                "actual": {
                    "status": structure.get("status"),
                    "verified": structure.get("verified"),
                    "retryable": structure.get("retryable"),
                    "rollback_performed": any(
                        item.get("details", {}).get("rollback_performed") is True
                        for item in events
                    ),
                },
                "events": events,
                # Arbitrary exception text may contain document content. Store
                # only a deterministic structural label and a one-way hash.
                "error_template": error_template,
                "error_signature": _hash(
                    record.get("error") or record.get("response")
                ),
                "environment": environment,
                "analysis": {
                    "category": category,
                    "headline": guidance["headline"],
                    "cause_candidates": list(guidance["causes"]),
                    "recommended_fix": guidance["fix"],
                    "confidence": guidance["confidence"],
                },
                "reproduction": {
                    "synthetic_fixture_required": True,
                    "mode": structure.get("mode"),
                    "action": structure.get("action"),
                    "operation": structure.get("operation"),
                    "app_type": structure.get("app_type"),
                    "document_extension": structure.get("document_extension"),
                    "failed_step": structure.get("failed_step"),
                    "event_sequence": [
                        {"action": item["action"], "status": item["status"]}
                        for item in events
                    ],
                },
                "regression_tests": list(guidance["tests"]),
                "raw_content_stored": False,
                "automatic_code_change": False,
                "running_binary_mutated": False,
                "requires_user_approval_for_remediation": True,
            }
            self._data["incidents"].append(incident)
            self._data["incidents"] = self._data["incidents"][-self.max_incidents:]
            self._save_locked()
            return copy.deepcopy(incident)

    def list_incidents(self, limit=20, *, status=None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 20), 200))
        if status is not None and str(status) not in INCIDENT_STATUSES:
            raise DiagnosticIncidentError("진단 사건 상태가 올바르지 않습니다.")
        with self._lock:
            values = [
                copy.deepcopy(item) for item in reversed(self._data["incidents"])
                if status is None or item.get("status") == status
            ]
        return values[:limit]

    def get(self, incident_id) -> dict[str, Any]:
        identifier = str(incident_id or "")
        with self._lock:
            item = next((
                value for value in self._data["incidents"]
                if value.get("incident_id") == identifier
            ), None)
            if item is None:
                raise DiagnosticIncidentError("진단 사건을 찾을 수 없습니다.")
            return copy.deepcopy(item)

    def latest(self) -> dict[str, Any] | None:
        values = self.list_incidents(1)
        return values[0] if values else None

    def set_status(self, incident_id, status) -> dict[str, Any]:
        clean_status = str(status or "").casefold()
        if clean_status not in INCIDENT_STATUSES:
            raise DiagnosticIncidentError("진단 사건 상태가 올바르지 않습니다.")
        with self._lock:
            item = next((
                value for value in self._data["incidents"]
                if value.get("incident_id") == str(incident_id or "")
            ), None)
            if item is None:
                raise DiagnosticIncidentError("진단 사건을 찾을 수 없습니다.")
            item["status"] = clean_status
            item["status_changed_at"] = _timestamp()
            self._save_locked()
            return copy.deepcopy(item)

    def remediation_spec(self, incident_id) -> dict[str, Any]:
        incident = self.get(incident_id)
        category = incident["analysis"]["category"]
        return {
            "incident_id": incident["incident_id"],
            "status": "proposal_only",
            "objective": incident["analysis"]["recommended_fix"],
            "cause_category": category,
            "required_regression_tests": list(incident["regression_tests"]),
            "execution_order": [
                "사용자 승인",
                "현재 실행본과 분리된 새 Git worktree 생성",
                "익명화된 소유 fixture 실패 재현 테스트 작성",
                "최소 코드 수정",
                "관련 테스트와 전체 회귀 실행",
                "사용자 승인 후 새 EXE 빌드",
                "검증 실패 시 기존 current 실행본 유지",
            ],
            "branch_prefix": f"codex/diagnostic-{incident['incident_id'][:8]}",
            "worktree_required": True,
            "test_first_required": True,
            "full_regression_required": True,
            "build_requires_separate_approval": True,
            "rollback_strategy": "기존 current 실행본을 유지하고 새 빌드 승격을 취소",
            "automatic_execution_allowed": False,
            "running_binary_mutation_allowed": False,
        }

    def report(self, incident_id) -> dict[str, Any]:
        incident = self.get(incident_id)
        return {
            "incident_id": incident["incident_id"],
            "status": incident["status"],
            "occurrence_count": incident["occurrence_count"],
            "where": {
                "action": incident["interpreted"].get("action"),
                "operation": incident["interpreted"].get("operation"),
                "failed_step": incident["interpreted"].get("failed_step"),
                "app_type": incident["interpreted"].get("app_type"),
            },
            "why": copy.deepcopy(incident["analysis"]),
            "expected": copy.deepcopy(incident["expected"]),
            "actual": copy.deepcopy(incident["actual"]),
            "reproduction": copy.deepcopy(incident["reproduction"]),
            "environment": copy.deepcopy(incident["environment"]),
            "remediation": self.remediation_spec(incident_id),
            "privacy": {
                "raw_content_stored": False,
                "input_represented_by_hash": True,
                "document_content_collected": False,
                "paths_collected": False,
            },
        }

    def health_summary(self) -> dict[str, Any]:
        with self._lock:
            incidents = copy.deepcopy(self._data["incidents"])
        categories = Counter(
            item.get("analysis", {}).get("category", "unknown") for item in incidents
        )
        return {
            "incident_groups": len(incidents),
            "total_occurrences": sum(int(item.get("occurrence_count") or 0) for item in incidents),
            "open_incidents": sum(item.get("status") in {"new", "acknowledged"} for item in incidents),
            "categories": dict(sorted(categories.items())),
            "automatic_code_change": False,
            "running_binary_mutation": False,
        }
