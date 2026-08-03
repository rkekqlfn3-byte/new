"""Quality rules and lifecycle states for learned macros."""

import difflib
import re

from engine.execution_result import ERROR_TYPES, normalize_error_type

LEARNED_STATES = {"active", "needs_review", "broken", "disabled"}
FAILURE_TYPES = set(ERROR_TYPES)
SLOT_RE = re.compile(r"\{[0-9a-zA-Z가-힣_]+\}")


def normalize_trigger(value):
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text.strip(".,!?·'\"")[:180]


def looks_like_internal_trigger(value, macro_name=""):
    text = normalize_trigger(value)
    name = str(macro_name or "").strip()
    if not text or SLOT_RE.search(text):
        return False
    ascii_identifier = bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", text))
    internal_shape = ascii_identifier and (
        "_" in text or "-" in text or any(char.isupper() for char in str(value))
    )
    generic = bool(re.fullmatch(r"(?:learned_?macro|macro|action)(?:_?\d+)?", text))
    same_internal_name = internal_shape and text == name.lower()
    return generic or same_internal_name


def clean_trigger_list(values, macro_name=""):
    cleaned = []
    for value in values if isinstance(values, list) else []:
        text = normalize_trigger(value)
        if not text or looks_like_internal_trigger(text, macro_name):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:10]


def _comparable(value):
    if SLOT_RE.search(value):
        return ""
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", value.lower())


def find_trigger_conflicts(learned_macros, utterances, exclude=None):
    """Return exact or very-close trigger conflicts with another learned macro."""
    exclude = tuple(exclude or (None, None))
    incoming = clean_trigger_list(utterances)
    conflicts = []
    for app_name, app_macros in (learned_macros or {}).items():
        if not isinstance(app_macros, dict):
            continue
        for macro_name, record in app_macros.items():
            if (app_name, macro_name) == exclude or not isinstance(record, dict):
                continue
            learning = record.get("learning", {})
            existing = clean_trigger_list(
                (learning.get("utterances", []) if isinstance(learning, dict) else [])
                or record.get("utterances", []),
                macro_name,
            )
            for new_text in incoming:
                for old_text in existing:
                    if new_text == old_text:
                        conflicts.append({
                            "type": "exact", "utterance": new_text,
                            "app": app_name, "macro": macro_name, "score": 1.0,
                        })
                        break
                    new_compact = _comparable(new_text)
                    old_compact = _comparable(old_text)
                    if min(len(new_compact), len(old_compact)) < 6:
                        continue
                    ratio = difflib.SequenceMatcher(None, new_compact, old_compact).ratio()
                    if ratio >= 0.94:
                        conflicts.append({
                            "type": "similar", "utterance": new_text,
                            "app": app_name, "macro": macro_name,
                            "existing": old_text, "score": round(ratio, 3),
                        })
                        break
    return conflicts


def ensure_quality_fields(record, macro_name=""):
    if not isinstance(record, dict):
        return False
    changed = False
    learning = record.get("learning", {})
    raw_utterances = (
        learning.get("utterances", []) if isinstance(learning, dict) else []
    ) or record.get("utterances", [])
    safe = clean_trigger_list(raw_utterances, macro_name)
    state = record.get("state")
    if state not in LEARNED_STATES:
        record["state"] = "active" if safe else "needs_review"
        changed = True
    defaults = {
        "consecutive_failures": 0,
        "last_failure_type": "",
        "state_reason": "" if safe else "사용 가능한 발동 문장을 확인해야 합니다.",
    }
    for key, value in defaults.items():
        if key not in record:
            record[key] = value
            changed = True
    return changed


def apply_execution_result(record, success, failure_type="unknown"):
    ensure_quality_fields(record)
    if success:
        record["consecutive_failures"] = 0
        record["last_failure_type"] = ""
        record["state_reason"] = ""
        if record.get("state") in {"needs_review", "broken"}:
            record["state"] = "active"
        return record["state"]

    failure_type = normalize_error_type(failure_type)
    record["last_failure_type"] = failure_type
    if failure_type == "user_cancelled":
        return record["state"]
    if failure_type == "environment_error":
        record["state_reason"] = "환경 문제로 실행하지 못했습니다. 매크로 품질 실패에는 포함하지 않습니다."
        return record["state"]
    streak = int(record.get("consecutive_failures", 0) or 0) + 1
    record["consecutive_failures"] = streak
    if streak >= 3:
        if failure_type == "validation_error":
            record["state"] = "broken"
            record["state_reason"] = "검증 오류가 3회 연속 발생했습니다."
        else:
            record["state"] = "needs_review"
            record["state_reason"] = f"{failure_type} 실패가 3회 연속 발생했습니다."
    return record["state"]
