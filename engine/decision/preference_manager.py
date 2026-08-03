"""Persistent, conservative learning for choices between safe action methods."""

from __future__ import annotations

import copy
import re
import threading
from datetime import datetime

from engine.runtime_paths import user_data_path
from engine.storage.json_store import atomic_write_json, safe_read_json

PREFERENCE_SCHEMA_VERSION = 1
DEFAULT_PREFERENCE_PATH = user_data_path("user_preferences.json")
PREFERENCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
METHOD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _non_negative_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


class PreferenceManager:
    """Learn recommendations gradually and auto-apply only at high confidence."""

    def __init__(
        self,
        path=None,
        recommend_after=3,
        auto_apply_after=5,
        auto_confidence=0.85,
        disable_after_failures=2,
    ):
        self.path = str(path or DEFAULT_PREFERENCE_PATH)
        self.recommend_after = max(1, int(recommend_after))
        self.auto_apply_after = max(self.recommend_after, int(auto_apply_after))
        self.auto_confidence = max(0.5, min(float(auto_confidence), 1.0))
        self.disable_after_failures = max(1, int(disable_after_failures))
        self._lock = threading.RLock()
        self._data = self._normalize(safe_read_json(self.path, self._default_data()))

    @staticmethod
    def _default_data():
        return {"schema_version": PREFERENCE_SCHEMA_VERSION, "preferences": {}}

    @staticmethod
    def _clean_key(value):
        key = str(value or "").strip().casefold()
        if not PREFERENCE_KEY_RE.fullmatch(key):
            raise ValueError(f"선호 키가 올바르지 않습니다: {value!r}")
        return key

    @staticmethod
    def _clean_method(value):
        method = str(value or "").strip().casefold()
        if not METHOD_RE.fullmatch(method):
            raise ValueError(f"선호 방식이 올바르지 않습니다: {value!r}")
        return method

    @classmethod
    def _normalize_record(cls, value):
        raw = value if isinstance(value, dict) else {}
        counts = {}
        for method, count in (
            raw.get("method_counts", {}).items()
            if isinstance(raw.get("method_counts"), dict) else []
        ):
            try:
                cleaned_method = cls._clean_method(method)
                cleaned_count = max(0, int(count))
            except (TypeError, ValueError):
                continue
            if cleaned_count:
                counts[cleaned_method] = cleaned_count
        preferred = str(raw.get("preferred_method") or "").strip().casefold()
        if preferred not in counts:
            preferred = max(counts, key=counts.get) if counts else ""
        selection_count = counts.get(preferred, 0)
        total = sum(counts.values())
        return {
            "preferred_method": preferred or None,
            "selection_count": selection_count,
            "alternative_count": max(0, total - selection_count),
            "confidence": round(selection_count / total, 4) if total else 0.0,
            "method_counts": counts,
            "locked": bool(raw.get("locked", False)),
            "auto_apply_enabled": bool(raw.get("auto_apply_enabled", True)),
            "failure_count": _non_negative_int(raw.get("failure_count", 0)),
            "last_selected_at": raw.get("last_selected_at"),
            "last_success_at": raw.get("last_success_at"),
            "last_failure_at": raw.get("last_failure_at"),
            "last_failure_method": raw.get("last_failure_method"),
        }

    @classmethod
    def _normalize(cls, value):
        raw = value if isinstance(value, dict) else {}
        preferences = {}
        source = raw.get("preferences", {})
        if isinstance(source, dict):
            for key, record in source.items():
                try:
                    clean_key = cls._clean_key(key)
                except ValueError:
                    continue
                preferences[clean_key] = cls._normalize_record(record)
        return {
            "schema_version": PREFERENCE_SCHEMA_VERSION,
            "preferences": preferences,
        }

    def _save_locked(self):
        atomic_write_json(self.path, self._data)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key):
        clean_key = self._clean_key(key)
        with self._lock:
            record = self._data["preferences"].get(clean_key)
            return copy.deepcopy(record) if record else None

    def decision(self, key):
        record = self.get(key)
        if not record or not record.get("preferred_method"):
            return {
                "mode": "ask",
                "preferred_method": None,
                "confidence": 0.0,
                "reason": "no_preference_data",
            }
        if not record.get("auto_apply_enabled", True):
            mode = "ask"
            reason = "auto_apply_disabled_after_failures"
        elif record.get("locked"):
            mode = "auto"
            reason = "explicitly_remembered"
        elif (
            record["selection_count"] >= self.auto_apply_after
            and record["confidence"] >= self.auto_confidence
        ):
            mode = "auto"
            reason = "high_confidence_preference"
        elif (
            record["selection_count"] >= self.recommend_after
            and record["alternative_count"] == 0
        ):
            mode = "recommend"
            reason = "repeated_consistent_choice"
        else:
            mode = "ask"
            reason = "insufficient_confidence"
        return {
            "mode": mode,
            "preferred_method": record["preferred_method"],
            "confidence": record["confidence"],
            "reason": reason,
            "selection_count": record["selection_count"],
            "alternative_count": record["alternative_count"],
            "failure_count": record["failure_count"],
        }

    def record_selection(self, key, method, remember=False):
        clean_key = self._clean_key(key)
        clean_method = self._clean_method(method)
        with self._lock:
            record = self._data["preferences"].get(clean_key, {})
            counts = dict(record.get("method_counts", {}))
            counts[clean_method] = counts.get(clean_method, 0) + 1
            highest = max(counts.values())
            leaders = [name for name, count in counts.items() if count == highest]
            preferred = clean_method if clean_method in leaders else leaders[0]
            total = sum(counts.values())
            selection_count = counts[preferred]
            normalized = self._normalize_record({
                **record,
                "preferred_method": preferred,
                "method_counts": counts,
                "locked": bool(remember),
                "auto_apply_enabled": True,
                "failure_count": 0,
                "last_selected_at": _now(),
                "last_success_at": _now(),
                "last_failure_method": None,
            })
            normalized["selection_count"] = selection_count
            normalized["alternative_count"] = total - selection_count
            normalized["confidence"] = round(selection_count / total, 4)
            self._data["preferences"][clean_key] = normalized
            self._save_locked()
            return copy.deepcopy(normalized)

    def record_success(self, key, method):
        clean_key = self._clean_key(key)
        clean_method = self._clean_method(method)
        with self._lock:
            record = self._data["preferences"].get(clean_key)
            if not record or record.get("preferred_method") != clean_method:
                return copy.deepcopy(record) if record else None
            record["failure_count"] = 0
            record["last_failure_method"] = None
            record["last_success_at"] = _now()
            self._save_locked()
            return copy.deepcopy(record)

    def record_failure(self, key, method):
        clean_key = self._clean_key(key)
        clean_method = self._clean_method(method)
        with self._lock:
            record = self._data["preferences"].get(clean_key)
            if not record or record.get("preferred_method") != clean_method:
                return {
                    "disabled": False,
                    "failure_count": 0,
                    "record": copy.deepcopy(record) if record else None,
                }
            if record.get("last_failure_method") == clean_method:
                record["failure_count"] = int(record.get("failure_count", 0)) + 1
            else:
                record["failure_count"] = 1
            record["last_failure_method"] = clean_method
            record["last_failure_at"] = _now()
            disabled = record["failure_count"] >= self.disable_after_failures
            if disabled:
                record["auto_apply_enabled"] = False
                record["locked"] = False
            self._save_locked()
            return {
                "disabled": disabled,
                "failure_count": record["failure_count"],
                "record": copy.deepcopy(record),
            }
