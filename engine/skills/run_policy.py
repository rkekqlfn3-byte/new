"""User-owned execution policy for learned JARVIS skills."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime

from engine.skills.postconditions import normalize_postconditions
from engine.skills.skill_profile import SkillProfile


RUN_POLICIES = frozenset({"confirm", "auto"})
POLICY_CHANGE_ACTORS = frozenset({"user", "migration", "system_default"})
AUTO_SUCCESS_THRESHOLD = 5

DIRECTIVE_STORED = "stored"
DIRECTIVE_CONFIRM = "force_confirm"
DIRECTIVE_RUN_ONCE = "force_run_once"
DIRECTIVE_PREVIEW = "preview"

_PREVIEW_PATTERNS = (
    "실행하지 말고 미리 보여줘", "실행하지 말고 보여줘", "미리 보여줘",
    "실행 전 보여줘", "preview only",
)
_CONFIRM_PATTERNS = (
    "확인하고 실행해", "확인 후 실행해", "물어보고 실행해",
    "confirm before running",
)
_RUN_ONCE_PATTERNS = (
    "이번에는 묻지 마", "이번엔 묻지 마", "바로 실행해",
    "확인 없이 실행해", "run now", "don't ask this time",
)

_VERIFIABLE_PLAN_ACTIONS = frozenset({
    "wait", "open_app", "focus_window", "move_window", "window_state",
    "clipboard_set", "clipboard_get", "copy_file", "create_folder",
    "write_text_file", "uia_select_file",
})
_HARD_CONFIRM_ACTIONS = frozenset({"move_file"})
_HARD_CONFIRM_OPERATIONS = frozenset({
    "delete", "remove", "find_replace", "sort_range", "save_as",
    "format_range", "insert_text", "set_text_format",
    "set_paragraph_format",
})
_RISK_WORDS = frozenset({
    "delete", "remove", "erase", "send", "upload", "email", "payment",
    "shutdown", "registry", "system_setting", "bulk", "mass", "wipe",
    "삭제", "제거", "전송", "업로드", "메일", "결제", "종료", "레지스트리",
    "시스템 설정", "대량", "일괄", "초기화",
})


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_run_directive(text):
    normalized = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    if any(pattern in normalized for pattern in _PREVIEW_PATTERNS):
        return DIRECTIVE_PREVIEW
    if any(pattern in normalized for pattern in _CONFIRM_PATTERNS):
        return DIRECTIVE_CONFIRM
    if any(pattern in normalized for pattern in _RUN_ONCE_PATTERNS):
        return DIRECTIVE_RUN_ONCE
    return DIRECTIVE_STORED


def strip_run_directive(text):
    value = str(text or "").strip()
    if parse_run_directive(value) == DIRECTIVE_STORED:
        return value
    patterns = (*_PREVIEW_PATTERNS, *_CONFIRM_PATTERNS, *_RUN_ONCE_PATTERNS)
    for pattern in sorted(patterns, key=len, reverse=True):
        value = re.sub(re.escape(pattern), " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" ,.!?·")


def ensure_run_policy_fields(record, *, changed_by="migration", changed_at=None):
    """Normalize legacy policy data in place and report whether it changed."""

    if not isinstance(record, dict):
        return False
    actor = changed_by if changed_by in POLICY_CHANGE_ACTORS else "migration"
    changed = False
    raw_policy = str(record.get("run_policy") or "").strip().casefold()
    if raw_policy not in RUN_POLICIES:
        previous = raw_policy if raw_policy else ""
        record["run_policy"] = "confirm"
        history = record.get("run_policy_history")
        if not isinstance(history, list):
            history = []
            record["run_policy_history"] = history
        history.append({
            "previous_policy": previous,
            "new_policy": "confirm",
            "changed_by": actor,
            "changed_at": changed_at or _now_iso(),
            "reason": (
                "새 학습 스킬의 기본 실행 정책"
                if actor == "system_default"
                else "기존 스킬의 안전한 기본 정책 마이그레이션"
            ),
        })
        changed = True
    elif record.get("run_policy") != raw_policy:
        record["run_policy"] = raw_policy
        changed = True

    history = record.get("run_policy_history")
    if not isinstance(history, list):
        record["run_policy_history"] = []
        changed = True
    for key in ("verified_success_count", "consecutive_verified_success"):
        try:
            value = max(0, int(record.get(key, 0) or 0))
        except (TypeError, ValueError):
            value = 0
        if record.get(key) != value:
            record[key] = value
            changed = True
    return changed


def change_run_policy(
    record, new_policy, *, changed_by="user", reason="", changed_at=None
):
    if not isinstance(record, dict):
        raise ValueError("실행 정책을 변경할 스킬 정보가 올바르지 않습니다.")
    ensure_run_policy_fields(record)
    policy = str(new_policy or "").strip().casefold()
    if policy not in RUN_POLICIES:
        raise ValueError("실행 정책은 confirm 또는 auto여야 합니다.")
    actor = str(changed_by or "").strip().casefold()
    if actor not in POLICY_CHANGE_ACTORS:
        raise ValueError("실행 정책 변경 주체가 올바르지 않습니다.")
    previous = record["run_policy"]
    if previous == policy:
        return False
    record["run_policy"] = policy
    record.setdefault("run_policy_history", []).append({
        "previous_policy": previous,
        "new_policy": policy,
        "changed_by": actor,
        "changed_at": changed_at or _now_iso(),
        "reason": str(reason or "사용자가 실행 정책 변경을 승인했습니다.")[:500],
    })
    return True


def skill_policy_fingerprint(skill):
    skill = skill if isinstance(skill, dict) else {}
    material = {
        "state": skill.get("state", "active"),
        "code": skill.get("code", ""),
        "plan": skill.get("plan", []),
        "native_plan": skill.get("native_plan", []),
        "uia_plan": skill.get("uia_plan", []),
        "execution_profile": skill.get("execution_profile", {}),
        "postconditions": skill.get("postconditions", []),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_plan(skill, route):
    return skill.get({
        "native": "native_plan",
        "action_plan": "plan",
        "uia": "uia_plan",
    }.get(route, ""), [])


def _risk_text(skill):
    learning = skill.get("learning", {})
    pieces = [skill.get("description", "")]
    if isinstance(learning, dict):
        pieces.extend([
            learning.get("intent", ""),
            " ".join(str(item) for item in learning.get("verbs", [])),
        ])
    return " ".join(str(item or "") for item in pieces).casefold()


def forced_confirmation_reasons(skill, route=None):
    skill = skill if isinstance(skill, dict) else {}
    route = route or SkillProfile.from_skill(skill).primary_route
    reasons = []
    if route == "python" or str(skill.get("code", "")).strip() and not route:
        reasons.append("dynamic_python")
    execution_profile = skill.get("execution_profile", {})
    if isinstance(execution_profile, dict):
        fallback_routes = execution_profile.get("fallback_routes", [])
        if isinstance(fallback_routes, (list, tuple)) and any(
            str(item or "").strip().casefold() == "python"
            for item in fallback_routes
        ):
            reasons.append("dynamic_python_fallback")

    plan = _route_plan(skill, route)
    if isinstance(plan, list):
        for step in plan:
            if not isinstance(step, dict):
                reasons.append("unverifiable_plan")
                continue
            action = str(step.get("action") or "")
            operation = str(step.get("operation") or "").casefold()
            if action in _HARD_CONFIRM_ACTIONS:
                reasons.append("hard_to_restore")
            if bool(step.get("overwrite")):
                reasons.append("file_overwrite")
            if operation in _HARD_CONFIRM_OPERATIONS:
                reasons.append("destructive_or_bulk_change")

    risk_text = _risk_text(skill)
    if any(word in risk_text for word in _RISK_WORDS):
        reasons.append("high_risk_intent")

    postconditions = normalize_postconditions(skill.get("postconditions", []))
    verification_capable = bool(postconditions)
    if not verification_capable and route in {"native", "action_plan"}:
        verification_capable = bool(plan) and all(
            isinstance(step, dict)
            and str(step.get("action") or "") in _VERIFIABLE_PLAN_ACTIONS
            for step in plan
        )
    if not verification_capable:
        reasons.append("no_verification_method")
    return tuple(dict.fromkeys(reasons))


@dataclass(frozen=True)
class RunPolicyAssessment:
    run_policy: str
    directive: str
    route: str
    requires_confirmation: bool
    preview_only: bool
    eligible_for_auto: bool
    suggest_auto: bool
    consecutive_verified_success: int
    forced_reasons: tuple[str, ...]
    reason: str

    def to_dict(self):
        data = asdict(self)
        data["forced_reasons"] = list(self.forced_reasons)
        return data


class SkillRunPolicyService:
    def __init__(self, threshold=AUTO_SUCCESS_THRESHOLD):
        self.threshold = max(1, int(threshold))

    def assess(self, skill, user_text=""):
        skill = skill if isinstance(skill, dict) else {}
        policy = str(skill.get("run_policy") or "confirm").casefold()
        if policy not in RUN_POLICIES:
            policy = "confirm"
        directive = parse_run_directive(user_text)
        route = SkillProfile.from_skill(skill).primary_route
        forced = forced_confirmation_reasons(skill, route)
        try:
            streak = max(
                0, int(skill.get("consecutive_verified_success", 0) or 0)
            )
        except (TypeError, ValueError):
            streak = 0
        eligible = not forced and route in {
            "native", "action_plan", "uia"
        } and streak >= self.threshold
        suggest = policy == "confirm" and eligible

        if directive == DIRECTIVE_PREVIEW:
            return RunPolicyAssessment(
                policy, directive, route, False, True, eligible, suggest,
                streak, forced, "explicit_preview",
            )
        if directive == DIRECTIVE_CONFIRM:
            requires = True
            reason = "explicit_confirmation"
        elif directive == DIRECTIVE_RUN_ONCE:
            requires = bool(forced)
            reason = "forced_safety_confirmation" if forced else "explicit_run_once"
        elif policy == "auto" and eligible:
            requires = False
            reason = "user_approved_auto"
        else:
            requires = True
            reason = (
                "forced_safety_confirmation" if forced
                else "stored_confirm_policy" if policy == "confirm"
                else "auto_eligibility_lost"
            )
        return RunPolicyAssessment(
            policy, directive, route, requires, False, eligible, suggest,
            streak, forced, reason,
        )

    def can_enable_auto(self, skill):
        assessment = self.assess(skill)
        return assessment.eligible_for_auto, assessment
