"""Local slot-template matcher for learned Jarvis commands."""

import re


SLOT_TOKEN_RE = re.compile(r"\{([0-9a-zA-Z가-힣_]+)\}")
DIRECTIONS = {
    "왼쪽", "오른쪽", "위", "아래", "가운데", "중앙", "좌측", "우측",
    "상단", "하단", "left", "right", "top", "bottom", "center",
}
COLORS = {
    "빨강", "빨간색", "파랑", "파란색", "노랑", "노란색", "초록", "초록색",
    "검정", "검은색", "흰색", "하양", "회색", "보라", "주황", "분홍",
    "red", "blue", "yellow", "green", "black", "white", "gray", "grey",
    "purple", "orange", "pink",
}


PARTICLE_ALTERNATIVES = (
    ("이라고", "라고"),
    ("으로", "로"),
    ("을", "를"),
    ("이", "가"),
    ("은", "는"),
    ("과", "와"),
)


def _literal_pattern(text, after_slot=False):
    particle_pattern = ""
    if after_slot:
        for first, second in PARTICLE_ALTERNATIVES:
            if text.startswith(first) or text.startswith(second):
                matched = first if text.startswith(first) else second
                particle_pattern = f"(?:{re.escape(first)}|{re.escape(second)})"
                text = text[len(matched):]
                break
    escaped = re.escape(text)
    return particle_pattern + re.sub(r"(?:\\ )+", r"\\s+", escaped)


def _compile_template(template, slot_types):
    parts = []
    groups = []
    position = 0
    for index, match in enumerate(SLOT_TOKEN_RE.finditer(template)):
        parts.append(_literal_pattern(template[position:match.start()], after_slot=bool(groups)))
        slot_name = match.group(1)
        group_name = f"slot_{index}"
        parts.append(fr"(?P<{group_name}>.+?)")
        groups.append((group_name, slot_name, slot_types.get(slot_name, "value")))
        position = match.end()
    parts.append(_literal_pattern(template[position:], after_slot=bool(groups)))
    if not groups:
        return None
    return re.compile("^" + "".join(parts) + "$", re.IGNORECASE), groups


def _valid_slot_value(value, slot_type, noun_dict):
    if not value:
        return False
    lowered = value.lower()
    if slot_type in {"app", "website"}:
        return lowered in noun_dict
    if slot_type == "direction":
        return lowered in DIRECTIONS
    if slot_type == "color":
        return lowered in COLORS or bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value))
    if slot_type == "number":
        return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "")))
    if slot_type == "cell":
        return bool(re.fullmatch(r"[a-zA-Z]{1,4}\d{1,7}", value))
    if slot_type == "url":
        return lowered.startswith(("http://", "https://"))
    return len(value) <= 200


class LearnedTemplateMatcher:
    def __init__(self):
        self._signature = None
        self._compiled = []

    def invalidate(self):
        self._signature = None
        self._compiled = []

    def _rebuild_if_needed(self, learned_macros):
        if self._signature is not None:
            return
        signature_parts = []
        records = []
        if isinstance(learned_macros, dict):
            for app_name, app_macros in learned_macros.items():
                if not isinstance(app_macros, dict):
                    continue
                for macro_name, macro in app_macros.items():
                    if not isinstance(macro, dict) or macro.get("state", "active") != "active":
                        continue
                    learning = macro.get("learning", {}) if isinstance(macro, dict) else {}
                    if not isinstance(learning, dict):
                        continue
                    utterances = learning.get("utterances", [])
                    slots = learning.get("slots", [])
                    if not isinstance(utterances, list) or not isinstance(slots, list):
                        continue
                    signature_parts.append((app_name, macro_name, repr(utterances), repr(slots)))
                    records.append((app_name, macro_name, learning, utterances, slots))

        signature = tuple(signature_parts)
        compiled = []
        for app_name, macro_name, learning, utterances, slots in records:
            slot_types = {
                item.get("name"): item.get("type", "value")
                for item in slots if isinstance(item, dict) and item.get("name")
            }
            for template in utterances:
                if not isinstance(template, str) or not SLOT_TOKEN_RE.search(template):
                    continue
                result = _compile_template(template.strip().lower(), slot_types)
                if not result:
                    continue
                regex, groups = result
                literal_length = len(SLOT_TOKEN_RE.sub("", template))
                compiled.append({
                    "app": app_name,
                    "macro": macro_name,
                    "template": template,
                    "regex": regex,
                    "groups": groups,
                    "learning": learning,
                    "score": (literal_length, -len(groups)),
                })
        self._compiled = sorted(compiled, key=lambda item: item["score"], reverse=True)
        self._signature = signature

    def match(self, text, learned_macros, noun_dict):
        self._rebuild_if_needed(learned_macros)
        normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
        normalized_nouns = {str(key).strip().lower(): value for key, value in noun_dict.items()}

        for candidate in self._compiled:
            match = candidate["regex"].fullmatch(normalized)
            if not match:
                continue
            values = {}
            valid = True
            for group_name, slot_name, slot_type in candidate["groups"]:
                value = re.sub(r"\s+", " ", match.group(group_name).strip())
                if not _valid_slot_value(value, slot_type, normalized_nouns):
                    valid = False
                    break
                if slot_name in values and values[slot_name] != value:
                    valid = False
                    break
                values[slot_name] = value
            if valid:
                return {
                    "macro": candidate["macro"],
                    "app": candidate["app"],
                    "template": candidate["template"],
                    "slots": values,
                    "learning": candidate["learning"],
                }
        return None
