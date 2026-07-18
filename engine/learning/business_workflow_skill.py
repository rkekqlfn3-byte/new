"""Approved, content-free reuse templates for the bounded business workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime
from typing import Any, Mapping

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json
from engine.workflow_step_registry import (
    WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION,
    report_workflow_step_order,
    report_workflow_step_recipe,
    validate_report_workflow_step_recipe,
)


WORKFLOW_SKILL_SCHEMA_VERSION = 2
DEFAULT_WORKFLOW_SKILL_PATH = user_data_path("business_workflow_skills.json")
EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class BusinessWorkflowSkillError(ValueError):
    pass


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_template(
    value: Mapping[str, Any],
    *,
    allow_legacy_recipe=False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BusinessWorkflowSkillError("업무 스킬 구조가 올바르지 않습니다.")
    report_format = str(value.get("report_format") or "").strip().casefold()
    try:
        expected_step_order = report_workflow_step_order(report_format)
    except ValueError as error:
        raise BusinessWorkflowSkillError(
            "업무 스킬의 보고서 형식이 올바르지 않습니다."
        ) from error
    try:
        slide_count = int(value.get("slide_count"))
    except (TypeError, ValueError) as error:
        raise BusinessWorkflowSkillError("업무 스킬의 PPT 장수가 올바르지 않습니다.") from error
    if not 3 <= slide_count <= 20:
        raise BusinessWorkflowSkillError("업무 스킬의 PPT 장수는 3~20장이어야 합니다.")
    step_order = tuple(str(item) for item in value.get("step_order") or ())
    if step_order != expected_step_order:
        raise BusinessWorkflowSkillError("업무 스킬의 단계 구성이 허용 계약과 다릅니다.")
    if allow_legacy_recipe and "step_recipe" not in value:
        step_recipe = report_workflow_step_recipe(report_format)
    else:
        try:
            step_recipe = validate_report_workflow_step_recipe(
                value.get("step_recipe"),
                report_format,
            )
        except ValueError as error:
            raise BusinessWorkflowSkillError(
                "업무 스킬의 단계 레시피가 허용 목록과 다릅니다."
            ) from error
        if (
            value.get("step_registry_schema_version")
            != WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION
        ):
            raise BusinessWorkflowSkillError(
                "업무 스킬의 단계 레지스트리 버전이 올바르지 않습니다."
            )
    if value.get("read_only_source") is not True:
        raise BusinessWorkflowSkillError("업무 스킬은 Excel 원본 읽기 전용이어야 합니다.")
    if value.get("fresh_outputs_each_run") is not True:
        raise BusinessWorkflowSkillError("업무 스킬은 실행마다 새 산출물을 만들어야 합니다.")
    if value.get("requires_approval_each_run") is not True:
        raise BusinessWorkflowSkillError("업무 스킬은 실행마다 승인을 요구해야 합니다.")
    return {
        "kind": "business_report",
        "report_format": report_format,
        "slide_count": slide_count,
        "step_order": list(step_order),
        "step_registry_schema_version": WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION,
        "step_recipe": step_recipe,
        "read_only_source": True,
        "fresh_outputs_each_run": True,
        "requires_approval_each_run": True,
    }


def _template_id(template: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(template).encode("utf-8")).hexdigest()[:24]


def _legacy_template_id(template: Mapping[str, Any]) -> str:
    legacy = {
        key: copy.deepcopy(template[key])
        for key in (
            "kind",
            "report_format",
            "slide_count",
            "step_order",
            "read_only_source",
            "fresh_outputs_each_run",
            "requires_approval_each_run",
        )
    }
    return _template_id(legacy)


def workflow_skill_template(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only reusable structure from one verified workflow result."""
    if (
        not isinstance(result, Mapping)
        or result.get("verified") is not True
        or result.get("step_contracts_verified") is not True
        or result.get("registered_step_recipe_verified") is not True
        or result.get("step_registry_schema_version")
        != WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION
    ):
        raise BusinessWorkflowSkillError("검증되지 않은 업무 결과는 스킬 후보가 될 수 없습니다.")
    report_format = str(result.get("report_format") or "").strip().casefold()
    step_order = tuple(str(item) for item in result.get("successful_steps") or ())
    try:
        step_contract_count = int(result.get("step_contract_count") or 0)
    except (TypeError, ValueError) as error:
        raise BusinessWorkflowSkillError(
            "검증된 단계 계약 수가 올바르지 않습니다."
        ) from error
    if step_contract_count != len(step_order):
        raise BusinessWorkflowSkillError("검증된 단계 계약 수가 업무 결과와 다릅니다.")
    try:
        step_recipe = report_workflow_step_recipe(report_format)
    except ValueError as error:
        raise BusinessWorkflowSkillError(
            "업무 스킬의 보고서 형식이 올바르지 않습니다."
        ) from error
    return _validate_template({
        "report_format": report_format,
        "slide_count": result.get("slide_count"),
        "step_order": step_order,
        "step_registry_schema_version": WORKFLOW_STEP_REGISTRY_SCHEMA_VERSION,
        "step_recipe": step_recipe,
        "read_only_source": True,
        "fresh_outputs_each_run": True,
        "requires_approval_each_run": True,
    })


class BusinessWorkflowSkillManager:
    """Persist one approved structural workflow without paths or document data."""

    def __init__(self, path=None):
        self.path = str(path or DEFAULT_WORKFLOW_SKILL_PATH)
        self._lock = threading.RLock()
        self._data = self._normalize(
            safe_read_json(self.path, self._default_data())
        )

    @staticmethod
    def _default_data() -> dict[str, Any]:
        return {
            "schema_version": WORKFLOW_SKILL_SCHEMA_VERSION,
            "candidates": {},
            "active_skill": None,
        }

    @classmethod
    def _normalize(cls, value) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        try:
            stored_schema_version = int(raw.get("schema_version") or 0)
        except (TypeError, ValueError):
            stored_schema_version = 0
        if stored_schema_version not in {1, WORKFLOW_SKILL_SCHEMA_VERSION}:
            raw = {}
            stored_schema_version = WORKFLOW_SKILL_SCHEMA_VERSION
        legacy_schema = stored_schema_version == 1
        candidates = {}
        candidate_id_map = {}
        records = (
            raw.get("candidates", {}).items()
            if isinstance(raw.get("candidates"), dict)
            else ()
        )
        for identifier, record in records:
            if not isinstance(record, dict):
                continue
            raw_template = record.get("template") or {}
            if legacy_schema and (
                "step_recipe" in raw_template
                or "step_registry_schema_version" in raw_template
            ):
                continue
            try:
                template = _validate_template(
                    raw_template,
                    allow_legacy_recipe=legacy_schema,
                )
            except (TypeError, ValueError):
                continue
            expected_id = _template_id(template)
            accepted_id = (
                _legacy_template_id(template) if legacy_schema else expected_id
            )
            if str(identifier) != accepted_id:
                continue
            candidate_id_map[str(identifier)] = expected_id
            status = str(record.get("status") or "candidate").casefold()
            if status not in {"candidate", "active", "dismissed"}:
                status = "candidate"
            evidence_ids = [
                str(item)
                for item in list(record.get("evidence_ids") or [])[-100:]
                if re.fullmatch(r"[a-f0-9]{24}", str(item))
            ]
            candidates[expected_id] = {
                "candidate_id": expected_id,
                "template": template,
                "status": status,
                "verified_success_count": max(
                    len(evidence_ids),
                    int(record.get("verified_success_count") or 0),
                ),
                "evidence_ids": evidence_ids,
                "first_verified_at": record.get("first_verified_at"),
                "last_verified_at": record.get("last_verified_at"),
                "approved_at": record.get("approved_at"),
                "dismissed_at": record.get("dismissed_at"),
            }
        active = raw.get("active_skill")
        normalized_active = None
        if isinstance(active, dict):
            raw_active_id = str(active.get("candidate_id") or "")
            candidate = candidates.get(
                candidate_id_map.get(raw_active_id, raw_active_id)
            )
            if candidate:
                raw_active_template = active.get("template") or {}
                if legacy_schema and (
                    "step_recipe" in raw_active_template
                    or "step_registry_schema_version" in raw_active_template
                ):
                    raw_active_template = None
                try:
                    template = _validate_template(
                        raw_active_template or {},
                        allow_legacy_recipe=legacy_schema,
                    )
                except (TypeError, ValueError):
                    template = None
                if template == candidate["template"] and active.get("user_confirmed") is True:
                    normalized_active = {
                        "candidate_id": candidate["candidate_id"],
                        "template": template,
                        "approved_at": active.get("approved_at"),
                        "user_confirmed": True,
                        "verified_success_count": max(
                            int(active.get("verified_success_count") or 0),
                            candidate["verified_success_count"],
                        ),
                    }
                    candidate["status"] = "active"
        return {
            "schema_version": WORKFLOW_SKILL_SCHEMA_VERSION,
            "candidates": candidates,
            "active_skill": normalized_active,
        }

    @staticmethod
    def _evidence_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _public_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(record.get(key))
            for key in (
                "candidate_id",
                "template",
                "status",
                "verified_success_count",
                "first_verified_at",
                "last_verified_at",
                "approved_at",
                "dismissed_at",
            )
        }

    def _save_locked(self) -> None:
        atomic_write_json(self.path, self._data, max_versions=3)

    def record_verified_success(
        self,
        result: Mapping[str, Any],
        *,
        evidence_id,
    ) -> dict[str, Any]:
        clean_evidence = str(evidence_id or "").strip()
        if not EVIDENCE_ID_RE.fullmatch(clean_evidence):
            raise BusinessWorkflowSkillError("업무 스킬 증거 ID가 올바르지 않습니다.")
        template = workflow_skill_template(result)
        identifier = _template_id(template)
        evidence_digest = self._evidence_digest(clean_evidence)
        now = _timestamp()
        with self._lock:
            record = self._data["candidates"].get(identifier)
            if record is None:
                record = {
                    "candidate_id": identifier,
                    "template": template,
                    "status": "candidate",
                    "verified_success_count": 0,
                    "evidence_ids": [],
                    "first_verified_at": now,
                    "last_verified_at": now,
                    "approved_at": None,
                    "dismissed_at": None,
                }
                self._data["candidates"][identifier] = record
            duplicate = evidence_digest in record["evidence_ids"]
            if not duplicate:
                record["evidence_ids"].append(evidence_digest)
                record["evidence_ids"] = record["evidence_ids"][-100:]
                record["verified_success_count"] = int(
                    record.get("verified_success_count") or 0
                ) + 1
                record["last_verified_at"] = now
            active = self._data.get("active_skill")
            if active and active.get("candidate_id") == identifier:
                record["status"] = "active"
                active["verified_success_count"] = record["verified_success_count"]
            elif record.get("status") == "dismissed" and not duplicate:
                record["status"] = "candidate"
                record["dismissed_at"] = None
            # Bound local growth without changing the active record.
            if len(self._data["candidates"]) > 20:
                removable = sorted(
                    (
                        item
                        for item in self._data["candidates"].values()
                        if item.get("status") != "active"
                        and item.get("candidate_id") != identifier
                    ),
                    key=lambda item: str(item.get("last_verified_at") or ""),
                )
                for item in removable[: len(self._data["candidates"]) - 20]:
                    self._data["candidates"].pop(item["candidate_id"], None)
            self._save_locked()
            result_view = self._public_candidate(record)
            result_view["duplicate_evidence"] = duplicate
            result_view["needs_confirmation"] = record.get("status") == "candidate"
            result_view["raw_paths_or_content_stored"] = False
            return result_view

    def latest_candidate(self) -> dict[str, Any] | None:
        with self._lock:
            candidates = [
                record
                for record in self._data["candidates"].values()
                if record.get("status") == "candidate"
            ]
            if not candidates:
                return None
            record = max(
                candidates,
                key=lambda item: str(item.get("last_verified_at") or ""),
            )
            return self._public_candidate(record)

    def activate(self, candidate_id) -> dict[str, Any]:
        identifier = str(candidate_id or "")
        with self._lock:
            candidate = self._data["candidates"].get(identifier)
            if not candidate or candidate.get("status") != "candidate":
                raise BusinessWorkflowSkillError("활성화할 검증 업무 스킬 후보가 없습니다.")
            previous = self._data.get("active_skill")
            if previous:
                previous_record = self._data["candidates"].get(
                    str(previous.get("candidate_id") or "")
                )
                if previous_record and previous_record.get("status") == "active":
                    previous_record["status"] = "candidate"
                    previous_record["approved_at"] = None
            now = _timestamp()
            active = {
                "candidate_id": identifier,
                "template": copy.deepcopy(candidate["template"]),
                "approved_at": now,
                "user_confirmed": True,
                "verified_success_count": candidate["verified_success_count"],
            }
            self._data["active_skill"] = active
            candidate["status"] = "active"
            candidate["approved_at"] = now
            candidate["dismissed_at"] = None
            self._save_locked()
            result = copy.deepcopy(active)
            result["replaced_previous"] = bool(
                previous and previous.get("candidate_id") != identifier
            )
            return result

    def dismiss(self, candidate_id) -> bool:
        identifier = str(candidate_id or "")
        with self._lock:
            candidate = self._data["candidates"].get(identifier)
            if not candidate or candidate.get("status") != "candidate":
                return False
            candidate["status"] = "dismissed"
            candidate["dismissed_at"] = _timestamp()
            self._save_locked()
            return True

    def deactivate(self) -> bool:
        with self._lock:
            active = self._data.get("active_skill")
            if not active:
                return False
            candidate = self._data["candidates"].get(active.get("candidate_id"))
            if candidate:
                candidate["status"] = "candidate"
                candidate["approved_at"] = None
            self._data["active_skill"] = None
            self._save_locked()
            return True

    def active_skill(self) -> dict[str, Any] | None:
        with self._lock:
            active = self._data.get("active_skill")
            return copy.deepcopy(active) if active else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            candidates = [
                self._public_candidate(record)
                for record in self._data["candidates"].values()
                if record.get("status") in {"candidate", "active"}
            ]
            candidates.sort(
                key=lambda item: str(item.get("last_verified_at") or ""),
                reverse=True,
            )
            return {
                "active_skill": copy.deepcopy(self._data.get("active_skill")),
                "candidates": candidates,
                "raw_paths_or_content_stored": False,
            }
