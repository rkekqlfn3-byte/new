"""Bounded Windows UI Automation discovery for classic desktop apps."""

from __future__ import annotations

import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass

import psutil
import win32clipboard
import win32con
import win32gui
from pywinauto import Desktop

from engine.hotkeys import press_hotkey


CLICKABLE_CONTROL_TYPES = frozenset({
    "button", "menuitem", "hyperlink", "listitem", "tabitem",
    "checkbox", "radiobutton", "combobox",
})
EDITABLE_CONTROL_TYPES = frozenset({"edit", "document"})
SELECTOR_FIELDS = frozenset({
    "automation_id", "name", "control_type", "parent_name",
    "ancestor_name", "match_mode",
})
MATCH_MODES = frozenset({"auto", "exact", "normalized", "contains"})
_TRAILING_PUNCTUATION = " .…⋯:：;；!?！？。·"


class UIAutomationError(RuntimeError):
    error_type = "execution_error"
    status = "failed"
    retryable = False


class UIAutomationTargetNotFound(UIAutomationError):
    error_type = "target_not_found"
    route_failure_code = "target_not_found"
    state_changed = False

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = dict(diagnostic or {})


class UIAutomationSearchTimeout(UIAutomationError):
    error_type = "timeout"
    route_failure_code = "uia_search_timeout"
    state_changed = False

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = dict(diagnostic or {})


class UIAutomationSearchLimit(UIAutomationError):
    error_type = "validation_error"
    status = "blocked"
    route_failure_code = "uia_search_limit"
    state_changed = False

    def __init__(self, message, diagnostic=None):
        super().__init__(message)
        self.diagnostic = dict(diagnostic or {})


class UIAutomationAmbiguousTarget(UIAutomationError):
    """Several controls have the same best locator score; never click one."""

    error_type = "validation_error"
    status = "confirmation_required"
    route_failure_code = "ambiguous_target"
    state_changed = False

    def __init__(self, message, candidates=None, diagnostic=None):
        super().__init__(message)
        self.candidates = [dict(item) for item in (candidates or [])]
        self.diagnostic = dict(diagnostic or {})
        self.error_code = "ambiguous_target"


@dataclass(frozen=True)
class UIASelector:
    automation_id: str = ""
    name: str = ""
    control_type: str = ""
    parent_name: str = ""
    ancestor_name: str = ""
    match_mode: str = "auto"
    legacy: bool = False

    @classmethod
    def from_value(cls, value):
        if isinstance(value, str) or value is None:
            return cls(name=str(value or ""), legacy=True)
        if not isinstance(value, dict):
            raise UIAutomationError("UI Automation 선택자는 문자열 또는 객체여야 합니다.")
        unknown = sorted(set(value) - SELECTOR_FIELDS)
        if unknown:
            raise UIAutomationError(
                "지원하지 않는 UI Automation 선택자 필드입니다: "
                + ", ".join(unknown)
            )
        fields = {}
        for name in SELECTOR_FIELDS - {"match_mode"}:
            raw = value.get(name, "")
            if not isinstance(raw, str):
                raise UIAutomationError(f"UI Automation 선택자 {name}은 문자열이어야 합니다.")
            fields[name] = raw.strip()
        match_mode = str(value.get("match_mode") or "auto").strip().casefold()
        if match_mode not in MATCH_MODES:
            raise UIAutomationError(
                "UI Automation match_mode는 auto/exact/normalized/contains 중 하나여야 합니다."
            )
        return cls(**fields, match_mode=match_mode, legacy=False)

    def cache_key(self):
        return (
            self.automation_id,
            self.name,
            self.control_type.casefold(),
            self.parent_name,
            self.ancestor_name,
            self.match_mode,
            self.legacy,
        )

    def to_dict(self):
        return {
            "automation_id": self.automation_id,
            "name": self.name,
            "control_type": self.control_type,
            "parent_name": self.parent_name,
            "ancestor_name": self.ancestor_name,
            "match_mode": self.match_mode,
        }


def normalize_accessible_name(value):
    """Normalize only deterministic UI label differences, never fuzzy-match."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("&", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(_TRAILING_PUNCTUATION)
    return text.casefold()


def _raw_accessible_name(value):
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))
    ).strip()


class WindowsUIAutomation:
    def __init__(
        self,
        noun_dict,
        *,
        search_timeout=1.5,
        max_search_depth=8,
        max_controls=600,
        max_searches_per_command=4,
        clock=None,
    ):
        self.noun_dict = noun_dict
        self.search_timeout = max(0.05, float(search_timeout))
        self.max_search_depth = max(1, min(int(max_search_depth), 20))
        self.max_controls = max(1, min(int(max_controls), 5000))
        self.max_searches_per_command = max(
            1, min(int(max_searches_per_command), 20)
        )
        self._clock = clock or time.monotonic
        self._search_count = 0
        self._control_cache = {}
        self.last_diagnostic = {}

    def begin_command(self):
        """Reset the bounded search budget while retaining valid window cache."""

        self._search_count = 0
        self.last_diagnostic = {}

    def _registered_path(self, app_name):
        for name, path in self.noun_dict.items():
            if str(name).strip().casefold() == str(app_name).strip().casefold():
                return str(path).strip('"')
        raise UIAutomationError(f"사전에 등록된 앱이 아닙니다: {app_name}")

    @staticmethod
    def _process_ids(path):
        executable = os.path.basename(path).lower().replace(".lnk", "").replace(".exe", "")
        if not executable:
            return set()
        matches = set()
        for process in psutil.process_iter(["pid", "name"]):
            try:
                name = (process.info.get("name") or "").lower().replace(".exe", "")
                if name == executable or executable in name or name in executable:
                    matches.add(process.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return matches

    @staticmethod
    def _preferred_window(candidates):
        """Prefer the foreground and most recently created matching window."""

        if not candidates:
            return None
        foreground = win32gui.GetForegroundWindow()

        def priority(window):
            try:
                created_at = psutil.Process(window.process_id()).create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                created_at = 0
            return (window.handle == foreground, created_at, window.handle)

        return max(candidates, key=priority)

    def find_window(self, app_name):
        path = self._registered_path(app_name)
        pids = self._process_ids(path)
        app_text = str(app_name).casefold()
        candidates = []
        title_matches = []
        for window in Desktop(backend="uia").windows():
            try:
                title = (window.window_text() or "").casefold()
                pid = window.process_id()
                if window.is_visible() and (pid in pids or app_text in title):
                    candidates.append(window)
                    if app_text and app_text in title:
                        title_matches.append(window)
            except Exception:
                continue
        if not candidates:
            raise UIAutomationTargetNotFound(
                f"실행 중인 UI Automation 창을 찾지 못했습니다: {app_name}"
            )
        return self._preferred_window(title_matches or candidates)

    @staticmethod
    def _window_fingerprint(window):
        try:
            handle = int(window.handle)
        except Exception:
            handle = 0
        try:
            pid = int(window.process_id())
        except Exception:
            pid = 0
        try:
            title = str(window.window_text() or "")
        except Exception:
            title = ""
        return handle, pid, title

    @staticmethod
    def _control_info(control):
        try:
            element = control.element_info
        except Exception:
            element = None
        try:
            name = str(control.window_text() or "")
        except Exception:
            name = str(getattr(element, "name", "") or "")
        return {
            "name": name.strip(),
            "control_type": str(
                getattr(element, "control_type", "") or ""
            ).strip(),
            "automation_id": str(
                getattr(element, "automation_id", "") or ""
            ).strip(),
        }

    @staticmethod
    def _parent(control):
        try:
            parent = control.parent()
            return None if parent is control else parent
        except Exception:
            return None

    def _ancestor_names(self, control):
        names = []
        current = self._parent(control)
        visited = set()
        for _ in range(self.max_search_depth):
            if current is None or id(current) in visited:
                break
            visited.add(id(current))
            name = self._control_info(current)["name"]
            if name:
                names.append(name)
            current = self._parent(current)
        return names

    def _parent_name(self, control):
        parent = self._parent(control)
        return self._control_info(parent)["name"] if parent is not None else ""

    @staticmethod
    def _available(control):
        try:
            return bool(control.is_visible() and control.is_enabled())
        except Exception:
            return False

    def _diagnostic(
        self,
        *,
        strategy,
        fallback_level,
        candidates,
        control=None,
        started=None,
    ):
        info = self._control_info(control) if control is not None else {
            "name": "", "control_type": "", "automation_id": "",
        }
        search_started = started if started is not None else self._clock()
        return {
            "locator_strategy": strategy,
            "fallback_level": int(fallback_level),
            "candidate_count": len(candidates),
            "matched_name": info["name"],
            "matched_control_type": info["control_type"],
            "matched_automation_id": info["automation_id"],
            "search_duration_ms": round(
                max(0.0, self._clock() - search_started) * 1000,
                2,
            ),
        }

    def _candidate_description(self, control):
        info = self._control_info(control)
        ancestors = self._ancestor_names(control)
        selector = {
            "automation_id": info["automation_id"],
            "name": info["name"],
            "control_type": info["control_type"],
            "parent_name": self._parent_name(control),
            "ancestor_name": ancestors[1] if len(ancestors) > 1 else "",
            "match_mode": "exact",
        }
        return {
            **info,
            "parent_name": selector["parent_name"],
            "ancestor_name": selector["ancestor_name"],
            "selector": selector,
        }

    def _ambiguous(self, candidates, diagnostic):
        descriptions = [
            self._candidate_description(control) for control in candidates[:8]
        ]
        labels = []
        for index, item in enumerate(descriptions, start=1):
            context = item.get("parent_name") or item.get("ancestor_name") or "상위 정보 없음"
            labels.append(
                f"{index}. {item.get('name') or '(이름 없음)'} "
                f"[{item.get('control_type') or 'Unknown'}] · {context}"
            )
        message = (
            "같은 조건의 UI 요소가 여러 개라 임의로 선택하지 않았습니다. "
            "아래 대상 중 하나를 선택해주세요.\n" + "\n".join(labels)
        )
        self.last_diagnostic = dict(diagnostic)
        raise UIAutomationAmbiguousTarget(
            message, candidates=descriptions, diagnostic=diagnostic
        )

    def _collect_controls(self, window, started):
        if self._search_count >= self.max_searches_per_command:
            diagnostic = self._diagnostic(
                strategy="search_limit", fallback_level=8,
                candidates=[], started=started,
            )
            raise UIAutomationSearchLimit(
                "한 명령에서 허용된 UI 요소 탐색 횟수를 초과했습니다.",
                diagnostic,
            )
        self._search_count += 1
        try:
            controls = list(window.descendants(depth=self.max_search_depth))
        except TypeError:
            controls = list(window.descendants())
        if self._clock() - started > self.search_timeout:
            diagnostic = self._diagnostic(
                strategy="timeout", fallback_level=8,
                candidates=[], started=started,
            )
            raise UIAutomationSearchTimeout(
                "UI 요소 탐색 시간이 제한을 초과했습니다.", diagnostic
            )
        truncated = len(controls) > self.max_controls
        return controls[:self.max_controls], truncated

    @staticmethod
    def _control_type_matches(info, selector, allowed_types):
        current = info["control_type"].casefold()
        requested = selector.control_type.casefold()
        return current in allowed_types and (not requested or current == requested)

    @staticmethod
    def _name_matches(info, selector, level):
        if not selector.name:
            return True
        current = info["name"]
        if level == "exact":
            return _raw_accessible_name(current) == _raw_accessible_name(selector.name)
        normalized_current = normalize_accessible_name(current)
        normalized_wanted = normalize_accessible_name(selector.name)
        if level == "normalized":
            return normalized_current == normalized_wanted
        return bool(normalized_wanted and normalized_wanted in normalized_current)

    def _structure_matches(self, control, selector, *, require_parent=False):
        if require_parent and selector.parent_name:
            if normalize_accessible_name(self._parent_name(control)) != normalize_accessible_name(
                selector.parent_name
            ):
                return False
        if selector.ancestor_name:
            wanted = normalize_accessible_name(selector.ancestor_name)
            if wanted not in {
                normalize_accessible_name(name)
                for name in self._ancestor_names(control)
            }:
                return False
        return True

    def _finalize(self, matches, strategy, level, started, selector, cache_key):
        diagnostic = self._diagnostic(
            strategy=strategy,
            fallback_level=level,
            candidates=matches,
            control=matches[0] if len(matches) == 1 else None,
            started=started,
        )
        if len(matches) > 1:
            self._ambiguous(matches, diagnostic)
        if len(matches) == 1:
            control = matches[0]
            self.last_diagnostic = dict(diagnostic)
            self._control_cache[cache_key] = (control, dict(diagnostic))
            while len(self._control_cache) > 32:
                self._control_cache.pop(next(iter(self._control_cache)))
            return control, diagnostic
        return None

    def _locate_control(self, window, selector_value="", editable=False):
        started = self._clock()
        selector = UIASelector.from_value(selector_value)
        allowed_types = (
            EDITABLE_CONTROL_TYPES if editable else CLICKABLE_CONTROL_TYPES
        )
        if (
            selector.control_type
            and selector.control_type.casefold() not in allowed_types
        ):
            raise UIAutomationError(
                f"이 작업에서 허용하지 않는 컨트롤 형식입니다: {selector.control_type}"
            )
        fingerprint = self._window_fingerprint(window)
        cache_key = (fingerprint, selector.cache_key(), bool(editable))
        cached = self._control_cache.get(cache_key)
        if cached and self._available(cached[0]):
            original = cached[1]
            diagnostic = {
                **original,
                "locator_strategy": f"cache:{original['locator_strategy']}",
                "fallback_level": 0,
                "candidate_count": 1,
                "search_duration_ms": round((self._clock() - started) * 1000, 2),
            }
            self.last_diagnostic = dict(diagnostic)
            return cached[0], diagnostic

        controls, truncated = self._collect_controls(window, started)
        if truncated:
            diagnostic = self._diagnostic(
                strategy="search_limit", fallback_level=8,
                candidates=[], started=started,
            )
            self.last_diagnostic = dict(diagnostic)
            raise UIAutomationSearchLimit(
                "UI 요소 수가 안전 탐색 제한을 초과해 대상을 확정하지 않았습니다.",
                diagnostic,
            )
        pool = []
        for control in controls:
            if self._clock() - started > self.search_timeout:
                diagnostic = self._diagnostic(
                    strategy="timeout", fallback_level=8,
                    candidates=[], started=started,
                )
                raise UIAutomationSearchTimeout(
                    "UI 요소 탐색 시간이 제한을 초과했습니다.", diagnostic
                )
            info = self._control_info(control)
            if (
                self._available(control)
                and self._control_type_matches(info, selector, allowed_types)
            ):
                pool.append(control)

        if selector.legacy:
            wanted = str(selector.name or "").strip().casefold()
            exact = [
                control for control in pool
                if self._control_info(control)["name"].strip().casefold() == wanted
            ] if wanted else []
            matches = exact or [
                control for control in pool
                if not wanted or wanted in self._control_info(control)["name"].casefold()
            ]
            found = self._finalize(
                matches, "legacy_name", 7, started, selector, cache_key
            )
            if found:
                return found
        else:
            has_structure = bool(selector.parent_name or selector.ancestor_name)
            if selector.automation_id:
                matches = [
                    control for control in pool
                    if self._control_info(control)["automation_id"] == selector.automation_id
                ]
                if len(matches) == 1 or (matches and not has_structure):
                    found = self._finalize(
                        matches, "automation_id", 1, started, selector, cache_key
                    )
                    if found:
                        return found

            exact = [
                control for control in pool
                if self._name_matches(self._control_info(control), selector, "exact")
            ]
            if len(exact) == 1 or (exact and not has_structure):
                found = self._finalize(
                    exact, "control_type_name_exact", 2,
                    started, selector, cache_key,
                )
                if found:
                    return found
            if selector.match_mode != "exact":
                normalized = [
                    control for control in pool
                    if self._name_matches(
                        self._control_info(control), selector, "normalized"
                    )
                ]
                if len(normalized) == 1 or (normalized and not has_structure):
                    found = self._finalize(
                        normalized, "control_type_name_normalized", 3,
                        started, selector, cache_key,
                    )
                    if found:
                        return found
            if selector.match_mode in {"auto", "contains"}:
                partial = [
                    control for control in pool
                    if self._name_matches(
                        self._control_info(control), selector, "contains"
                    )
                ]
                if len(partial) == 1 or (partial and not has_structure):
                    found = self._finalize(
                        partial, "control_type_name_contains", 4,
                        started, selector, cache_key,
                    )
                    if found:
                        return found

            if has_structure:
                structured_pool = [
                    control for control in pool
                    if not selector.automation_id or self._control_info(control)["automation_id"] == selector.automation_id
                ]
                name_levels = ["exact"]
                if selector.match_mode != "exact":
                    name_levels.append("normalized")
                if selector.match_mode in {"auto", "contains"}:
                    name_levels.append("contains")
                for name_level in name_levels:
                    structured = [
                        control for control in structured_pool
                        if self._name_matches(
                            self._control_info(control), selector, name_level
                        )
                    ]
                    if selector.parent_name:
                        structured = [
                            control for control in structured
                            if self._structure_matches(
                                control, selector, require_parent=True
                            )
                        ]
                        strategy = (
                            "parent_ancestor_context"
                            if selector.ancestor_name else "parent_context"
                        )
                        level = 5
                    else:
                        structured = [
                            control for control in structured
                            if self._structure_matches(control, selector)
                        ]
                        strategy = "ancestor_context"
                        level = 6
                    if not structured:
                        continue
                    found = self._finalize(
                        structured, strategy, level,
                        started, selector, cache_key,
                    )
                    if found:
                        return found

        diagnostic = self._diagnostic(
            strategy="not_found", fallback_level=8,
            candidates=[], started=started,
        )
        self.last_diagnostic = dict(diagnostic)
        kind = "입력창" if editable else "컨트롤"
        label = selector.name or selector.automation_id or "(구조화 선택자)"
        raise UIAutomationTargetNotFound(
            f"{kind}을 찾지 못했습니다: {label}", diagnostic
        )

    def _find_control(self, window, control_name="", editable=False):
        control, diagnostic = self._locate_control(
            window, control_name, editable=editable
        )
        self.last_diagnostic = dict(diagnostic)
        return control

    def click(self, app_name, selector):
        window = self.find_window(app_name)
        control, diagnostic = self._locate_control(
            window, selector, editable=False
        )
        control.click_input()
        return {
            "success": True,
            "status": "confirmation_required",
            "window": window.window_text(),
            "control": control.window_text(),
            **diagnostic,
        }

    def set_text(self, app_name, selector, text):
        window = self.find_window(app_name)
        control, diagnostic = self._locate_control(
            window, selector, editable=True
        )
        control.set_focus()
        try:
            control.set_edit_text(str(text))
        except Exception:
            press_hotkey(["ctrl", "a"])
            self._set_clipboard(str(text))
            press_hotkey(["ctrl", "v"])
        actual = ""
        try:
            actual = str(control.get_value())
        except Exception:
            try:
                actual = str(control.window_text())
            except Exception:
                pass
        verified = actual == str(text) if actual else False
        return {
            "success": True,
            "status": "verified" if verified else "confirmation_required",
            "window": window.window_text(),
            "control": control.window_text(),
            "value": actual,
            **diagnostic,
        }

    @staticmethod
    def _set_clipboard(text):
        opened = False
        try:
            win32clipboard.OpenClipboard()
            opened = True
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(str(text), win32con.CF_UNICODETEXT)
        finally:
            if opened:
                win32clipboard.CloseClipboard()

    @staticmethod
    def _normalized_path(path):
        return os.path.normcase(os.path.abspath(str(path)))

    @classmethod
    def _explorer_views_at(cls, directory):
        """Return Explorer UIA windows and COM views at one exact directory."""

        expected = cls._normalized_path(directory)
        matches = []
        try:
            from win32com.client import Dispatch

            shell = Dispatch("Shell.Application")
            for browser in shell.Windows():
                try:
                    current = browser.Document.Folder.Self.Path
                    if cls._normalized_path(current) != expected:
                        continue
                    window = Desktop(backend="uia").window(handle=int(browser.HWND))
                    if window.is_visible():
                        matches.append((window, browser))
                except Exception:
                    continue
        except Exception:
            return []
        return matches

    @classmethod
    def _preferred_explorer_view(cls, views):
        window = cls._preferred_window([item[0] for item in views])
        if window is None:
            return None
        return next(item for item in views if item[0].handle == window.handle)

    def _explorer_diagnostic(self, strategy, window, control, started, level=0):
        try:
            window_name = str(window.window_text() or "")
        except Exception:
            window_name = ""
        return {
            "locator_strategy": strategy,
            "fallback_level": level,
            "candidate_count": 1,
            "matched_name": str(control or ""),
            "matched_control_type": "ListItem",
            "matched_automation_id": "",
            "search_duration_ms": round((self._clock() - started) * 1000, 2),
            "window": window_name,
        }

    def select_explorer_file(self, path):
        started = self._clock()
        file_path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))
        if not os.path.isfile(file_path):
            raise UIAutomationTargetNotFound(
                f"선택할 파일을 찾지 못했습니다: {file_path}"
            )
        directory = os.path.dirname(file_path)
        views = self._explorer_views_at(directory)
        if not views:
            subprocess.Popen(
                ["explorer.exe", directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = self._clock() + 5.0
            while self._clock() < deadline and not views:
                time.sleep(0.1)
                views = self._explorer_views_at(directory)
        selected_view = self._preferred_explorer_view(views)
        if selected_view is None:
            raise UIAutomationTargetNotFound(
                f"대상 폴더의 파일 탐색기 창을 찾지 못했습니다: {directory}"
            )
        window, browser = selected_view
        window.set_focus()
        deadline = self._clock() + 3.0
        filename = os.path.basename(file_path).casefold()
        try:
            document = browser.Document
            item = document.Folder.ParseName(os.path.basename(file_path))
            if item is not None:
                document.SelectItem(item, 1 | 4 | 8 | 16)
                selected = [
                    self._normalized_path(entry.Path)
                    for entry in document.SelectedItems()
                ]
                if self._normalized_path(file_path) in selected:
                    diagnostic = self._explorer_diagnostic(
                        "explorer_com", window, os.path.basename(file_path), started
                    )
                    return {
                        "success": True,
                        "status": "verified",
                        "control": os.path.basename(file_path),
                        **diagnostic,
                    }
        except Exception:
            pass
        while self._clock() < deadline:
            for control in window.descendants(control_type="ListItem"):
                try:
                    if control.window_text().strip().casefold() == filename:
                        control.click_input()
                        selected = bool(control.is_selected()) if hasattr(control, "is_selected") else True
                        diagnostic = self._explorer_diagnostic(
                            "explorer_list_item", window, control.window_text(),
                            started, level=1,
                        )
                        return {
                            "success": True,
                            "status": "verified" if selected else "confirmation_required",
                            "control": control.window_text(),
                            **diagnostic,
                        }
                except Exception:
                    continue
            time.sleep(0.1)
        raise UIAutomationTargetNotFound(
            f"탐색기에서 파일 항목을 찾지 못했습니다: {filename}"
        )
