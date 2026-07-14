"""Normalization helpers for structured macro learning metadata."""

import re


ALLOWED_NOUN_TYPES = {"app", "website", "file", "folder", "value", "general"}
ALLOWED_SLOT_TYPES = {
    "app", "text", "number", "color", "direction", "path", "url", "cell", "value"
}
INTENT_RE = re.compile(r"[^0-9A-Z_]+")


DESCRIPTION_ENDINGS = (
    ("보여줍니다", "보여줘"),
    ("알려줍니다", "알려줘"),
    ("키웁니다", "키워줘"),
    ("줄입니다", "줄여줘"),
    ("채웁니다", "채워줘"),
    ("바꿉니다", "바꿔줘"),
    ("엽니다", "열어줘"),
    ("닫습니다", "닫아줘"),
    ("저장합니다", "저장해줘"),
    ("변경합니다", "변경해줘"),
    ("검색합니다", "검색해줘"),
    ("설정합니다", "설정해줘"),
    ("실행합니다", "실행해줘"),
    ("합니다", "해줘"),
)


def _unique_strings(values, limit=12, max_length=120):
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = re.sub(r"\s+", " ", value.strip().lower())[:max_length]
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _normalize_intent(value, fallback="LEARNED_ACTION"):
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    cleaned = INTENT_RE.sub("_", raw).strip("_")[:64]
    return cleaned or fallback


def suggest_trigger_from_description(value):
    """Turn a plain Korean action description into a reviewable command phrase."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^\[[^\]]+\]\s*", "", text).strip(" .,!?")
    if not text:
        return ""
    for ending, replacement in DESCRIPTION_ENDINGS:
        if text.endswith(ending):
            return f"{text[:-len(ending)]}{replacement}"[:180]
    return text[:180]


def infer_verbs_from_utterances(utterances):
    """Extract conservative final predicates when the AI omitted core verbs."""
    inferred = []
    for utterance in utterances if isinstance(utterances, list) else []:
        text = re.sub(r"\{[^{}]+\}", " ", str(utterance or ""))
        tokens = re.findall(r"[0-9a-zA-Z가-힣]+", text.lower())
        if not tokens:
            continue
        verb = tokens[-1]
        if verb in {"줘", "주세요"} and len(tokens) > 1:
            verb = f"{tokens[-2]}{verb}"
        if verb not in inferred:
            inferred.append(verb[:50])
        if len(inferred) >= 12:
            break
    return inferred


def normalize_learning_metadata(action, original_utterance=""):
    """Return a bounded, JSON-safe learning record from an AI action."""
    raw = action.get("learning", {}) if isinstance(action, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    utterances = _unique_strings(raw.get("utterances", []), limit=10, max_length=180)
    original = re.sub(r"\s+", " ", str(original_utterance or "").strip().lower())
    if original and original not in utterances:
        utterances.insert(0, original[:180])
    if not utterances and isinstance(action, dict):
        fallback = suggest_trigger_from_description(
            action.get("description") or action.get("desc")
        )
        if fallback:
            utterances.append(fallback.lower())

    verbs = _unique_strings(raw.get("verbs", []), limit=12, max_length=50)
    if not verbs:
        verbs = infer_verbs_from_utterances(utterances)

    nouns = []
    noun_seen = set()
    for item in raw.get("nouns", []) if isinstance(raw.get("nouns"), list) else []:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text", "")).strip().lower())[:80]
        canonical = re.sub(
            r"\s+", " ", str(item.get("canonical", "")).strip().lower()
        )[:80]
        noun_type = str(item.get("type", "general")).strip().lower()
        if noun_type not in ALLOWED_NOUN_TYPES:
            noun_type = "general"
        if not text or (text, canonical, noun_type) in noun_seen:
            continue
        noun_seen.add((text, canonical, noun_type))
        nouns.append({"text": text, "canonical": canonical, "type": noun_type})
        if len(nouns) >= 12:
            break

    slots = []
    slot_names = set()
    for item in raw.get("slots", []) if isinstance(raw.get("slots"), list) else []:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"[^0-9a-zA-Z가-힣_]+", "_", str(item.get("name", "")).strip())
        name = name.strip("_")[:50]
        if not name or name in slot_names:
            continue
        slot_type = str(item.get("type", "value")).strip().lower()
        if slot_type not in ALLOWED_SLOT_TYPES:
            slot_type = "value"
        value = str(item.get("value", "")).strip()[:200]
        slots.append({
            "name": name,
            "type": slot_type,
            "value": value,
            "required": bool(item.get("required", True)),
        })
        slot_names.add(name)
        if len(slots) >= 12:
            break

    return {
        "intent": _normalize_intent(raw.get("intent")),
        "argument_mode": "json" if str(raw.get("argument_mode", "")).lower() == "json" else "legacy",
        "verbs": verbs,
        "nouns": nouns,
        "utterances": utterances,
        "slots": slots,
    }


def literal_utterances(learning):
    """Return phrases safe for the current exact/substring matcher.

    Slot templates are stored now but become executable only when the template
    matcher is introduced in the next stage.
    """
    if not isinstance(learning, dict):
        return []
    return [
        text for text in _unique_strings(learning.get("utterances", []), max_length=180)
        if "{" not in text and "}" not in text
    ]
