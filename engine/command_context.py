"""Build small, relevant AI command contexts from the local dictionaries."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass


TEXT_RE = re.compile(r"[0-9a-zA-Z가-힣]+")


def _tokens(value):
    return [token.casefold() for token in TEXT_RE.findall(str(value or ""))]


def _compact(value):
    return "".join(_tokens(value))


def _string_list(value):
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(frozen=True)
class CommandContext:
    dictionary_context: str
    learned_macros_context: str
    allowed_apps: tuple[str, ...]
    allowed_macros: tuple[tuple[str, str], ...]
    app_candidates: int
    learned_candidates: int


class CommandContextBuilder:
    """Select only command-relevant apps and learned macros for the LLM."""

    def __init__(self, dict_mgr, max_apps=20, max_macros=8):
        self.dict_mgr = dict_mgr
        self.max_apps = max_apps
        self.max_macros = max_macros
        self._app_signature = None
        self._app_entries = ()

    @staticmethod
    def command_text(user_input):
        if isinstance(user_input, list):
            for message in reversed(user_input):
                if isinstance(message, dict) and message.get("role") != "assistant":
                    return str(message.get("content", ""))
            return ""
        return str(user_input or "")

    def _ensure_app_entries(self):
        nouns = self.dict_mgr.get_nouns()
        search_engines = getattr(self.dict_mgr, "search_engines_dict", {})
        signature = (
            id(nouns), len(nouns), getattr(self.dict_mgr, "noun_revision", 0),
            id(search_engines), len(search_engines),
        )
        if signature == self._app_signature:
            return

        entries = {}
        for name in nouns:
            cleaned = str(name).strip()
            if cleaned:
                entries.setdefault(cleaned.casefold(), (cleaned, "app"))
        for name in search_engines:
            cleaned = str(name).strip()
            if cleaned:
                key = cleaned.casefold()
                if key in entries:
                    entries[key] = (entries[key][0], "app/search")
                else:
                    entries[key] = (cleaned, "search")

        self._app_entries = tuple(
            (name, kind, _compact(name)) for name, kind in entries.values() if _compact(name)
        )
        self._app_signature = signature

    def select_apps(self, command):
        self._ensure_app_entries()
        command_compact = _compact(command)
        command_tokens = _tokens(command)
        favorites = {
            str(item).strip().casefold()
            for item in getattr(self.dict_mgr, "favorites", [])
            if str(item).strip()
        }
        ranked = []

        for name, kind, name_compact in self._app_entries:
            score = 0.0
            exact = False
            if len(name_compact) >= 2 and name_compact in command_compact:
                score = 1000.0 + len(name_compact)
                exact = True
            else:
                best_ratio = 0.0
                for token in command_tokens:
                    if abs(len(token) - len(name_compact)) > max(2, len(name_compact) // 3):
                        continue
                    best_ratio = max(
                        best_ratio,
                        difflib.SequenceMatcher(None, token, name_compact).ratio(),
                    )
                if command_compact and abs(len(command_compact) - len(name_compact)) <= max(
                    4, len(name_compact)
                ):
                    best_ratio = max(
                        best_ratio,
                        difflib.SequenceMatcher(None, command_compact, name_compact).ratio(),
                    )
                cutoff = 0.80 if len(name_compact) <= 2 else (
                    0.65 if len(name_compact) <= 4 else 0.78
                )
                if best_ratio >= cutoff:
                    score = best_ratio * 500.0

            if score:
                if name.casefold() in favorites:
                    score += 5.0
                ranked.append((not exact, -score, -len(name_compact), name.casefold(), name, kind))

        ranked.sort()
        return [
            {"name": name, "kind": kind}
            for _, _, _, _, name, kind in ranked[: self.max_apps]
        ]

    def _macro_metadata(self, app_name, macro_name, record):
        learning = record.get("learning", {})
        if not isinstance(learning, dict):
            learning = {}
        linked = getattr(self.dict_mgr, "macro_dict", {}).get(macro_name, {})
        if not isinstance(linked, dict):
            linked = {}
        utterances = _string_list(record.get("utterances"))
        for item in _string_list(learning.get("utterances")):
            if item not in utterances:
                utterances.append(item)
        for item in _string_list(linked.get("synonyms")):
            if item not in utterances:
                utterances.append(item)
        verbs = _string_list(learning.get("verbs")) or _string_list(linked.get("verbs"))
        nouns = []
        for item in learning.get("nouns", []):
            if not isinstance(item, dict):
                continue
            nouns.append({
                "text": str(item.get("text", "")).strip(),
                "canonical": str(item.get("canonical", "")).strip(),
                "type": str(item.get("type", "")).strip(),
            })
        slots = []
        raw_slots = learning.get("slots", []) or linked.get("slots", [])
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            slots.append({
                "name": str(item.get("name", "")).strip(),
                "type": str(item.get("type", "")).strip(),
                "value": item.get("value", ""),
                "required": bool(item.get("required", False)),
            })
        attempts = int(record.get("success_count", 0) or 0) + int(
            record.get("failure_count", 0) or 0
        )
        metadata = {
            "app": app_name,
            "macro_name": macro_name,
            "description": str(record.get("description", "")).strip()[:300],
            "intent": str(
                learning.get("intent", record.get("intent", linked.get("intent", "")))
            ).strip(),
            "verbs": verbs[:12],
            "nouns": nouns[:12],
            "utterances": utterances[:12],
            "slots": slots[:12],
            "verification_status": str(record.get("verification_status", "")).strip(),
            "usage_count": int(record.get("usage_count", 0) or 0),
            "success_rate": round(int(record.get("success_count", 0) or 0) / attempts, 3)
            if attempts else None,
            "last_used_at": str(record.get("last_used_at", "")).strip(),
        }
        return metadata

    def select_macros(self, command, selected_apps):
        command_compact = _compact(command)
        selected_app_names = {item["name"].casefold() for item in selected_apps}
        learned_macros = getattr(self.dict_mgr, "learned_macros", {})
        ranked = []

        if not isinstance(learned_macros, dict):
            return []
        for app_name, app_macros in learned_macros.items():
            if not isinstance(app_macros, dict):
                continue
            for macro_name, record in app_macros.items():
                if not isinstance(record, dict):
                    continue
                if record.get("state", "active") != "active":
                    continue
                metadata = self._macro_metadata(str(app_name), str(macro_name), record)
                score = 0.0
                semantic_score = 0.0
                exact = False
                macro_compact = _compact(macro_name)
                if macro_compact and macro_compact in command_compact:
                    semantic_score += 700.0 + len(macro_compact)
                    exact = True

                for utterance in metadata["utterances"]:
                    utterance_compact = _compact(utterance)
                    if not utterance_compact:
                        continue
                    if utterance_compact == command_compact:
                        semantic_score += 2000.0
                        exact = True
                    elif utterance_compact in command_compact:
                        semantic_score += 800.0
                        exact = True
                    else:
                        ratio = difflib.SequenceMatcher(
                            None, utterance_compact, command_compact
                        ).ratio()
                        if ratio >= 0.68:
                            semantic_score += ratio * 300.0

                for verb in metadata["verbs"]:
                    verb_compact = _compact(verb)
                    if verb_compact and verb_compact in command_compact:
                        semantic_score += 180.0
                for noun in metadata["nouns"]:
                    for value in (noun["text"], noun["canonical"]):
                        value_compact = _compact(value)
                        if value_compact and value_compact in command_compact:
                            semantic_score += 140.0
                            break
                app_compact = _compact(app_name)
                description_matches = {
                    token for token in _tokens(metadata["description"])
                    if len(token) >= 2 and token != app_compact and token in command_compact
                }
                semantic_score += min(len(description_matches), 4) * 60.0

                if semantic_score >= 100.0:
                    score = semantic_score
                    if str(app_name).casefold() in selected_app_names:
                        score += 220.0
                    usage = metadata["usage_count"]
                    success_rate = metadata["success_rate"] or 0.0
                    ranked.append((not exact, -score, -usage, -success_rate, app_name, macro_name, metadata))

        ranked.sort(key=lambda item: item[:-1])
        return [item[-1] for item in ranked[: self.max_macros]]

    def build(self, user_input):
        command = self.command_text(user_input)
        apps = self.select_apps(command)
        macros = self.select_macros(command, apps)
        app_payload = {
            "apps": apps,
            "rule": "open_app 및 행동 계획의 앱 target은 이 후보의 name만 사용",
        }
        macro_payload = {
            "macros": macros,
            "rule": "기존 매크로 실행/응용은 이 후보의 app과 macro_name 조합만 사용",
        }
        return CommandContext(
            dictionary_context=json.dumps(app_payload, ensure_ascii=False, separators=(",", ":")),
            learned_macros_context=json.dumps(
                macro_payload, ensure_ascii=False, separators=(",", ":")
            ),
            allowed_apps=tuple(item["name"] for item in apps),
            allowed_macros=tuple((item["app"], item["macro_name"]) for item in macros),
            app_candidates=len(apps),
            learned_candidates=len(macros),
        )
