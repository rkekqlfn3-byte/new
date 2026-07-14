"""Validated local action-plan executor for common Windows automation."""

import os
import re
import shutil
import tempfile
import time

import psutil
import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from engine.hotkeys import press_hotkey, resolve_key
from engine.action_registry import (
    ALLOWED_ACTIONS, action_spec, app_target_actions, retry_limit,
)
from engine.ui_automation import (
    MATCH_MODES,
    SELECTOR_FIELDS,
    UIAutomationError,
    WindowsUIAutomation,
)
from engine.execution_runtime import ExecutionCancelled
from engine.app_actions.base import PreparedAction


PLACEHOLDER_RE = re.compile(r"\{([0-9a-zA-Z가-힣_]+)\}")
MOVE_DIRECTIONS = {
    "왼쪽": "left_half", "좌측": "left_half", "왼쪽 절반": "left_half",
    "left": "left_half", "left_half": "left_half",
    "오른쪽": "right_half", "우측": "right_half", "오른쪽 절반": "right_half",
    "right": "right_half", "right_half": "right_half",
    "위": "top_half", "상단": "top_half", "위쪽 절반": "top_half", "top": "top_half",
    "아래": "bottom_half", "하단": "bottom_half", "아래쪽 절반": "bottom_half", "bottom": "bottom_half",
    "가운데": "center", "중앙": "center", "center": "center",
}
WINDOW_STATES = {
    "최대화": "maximize", "maximize": "maximize",
    "최소화": "minimize", "minimize": "minimize",
    "복원": "restore", "restore": "restore",
}


class ActionPlanError(ValueError):
    pass


class ActionConfirmationRequired(ActionPlanError):
    def __init__(self, message, action="file_action", target=None):
        super().__init__(message)
        self.action = action
        self.target = target
        self.status = "confirmation_required"
        self.error_type = "validation_error"
        self.retryable = False


class ActionPlanVerificationError(ActionPlanError):
    def __init__(self, message, completed=None, verification=None):
        super().__init__(message)
        self.completed = list(completed or [])
        self.verification = list(verification or [])


class ActionTargetNotFoundError(ActionPlanError):
    pass


def classify_action_error(error):
    explicit = getattr(error, "error_type", None)
    if explicit:
        return explicit
    if isinstance(error, ExecutionCancelled):
        return "user_cancelled"
    if isinstance(error, ActionPlanVerificationError):
        return "verification_error"
    if isinstance(error, ActionTargetNotFoundError):
        return "target_not_found"
    if isinstance(error, UIAutomationError):
        return "environment_error"
    if isinstance(error, FileNotFoundError):
        return "target_not_found"
    if isinstance(error, ActionPlanError):
        return "validation_error"
    if isinstance(error, OSError):
        return "environment_error"
    return "execution_error"


class ActionExecutor:
    def __init__(
        self, noun_dict, verification_timeout=1.5, controller=None,
        app_action_registry=None,
    ):
        self.noun_dict = noun_dict
        self.verification_timeout = verification_timeout
        self.ui_automation = WindowsUIAutomation(noun_dict)
        self.controller = controller
        self.app_action_registry = app_action_registry

    def _noun_index(self):
        return {str(name).strip().lower(): (name, path) for name, path in self.noun_dict.items()}

    @staticmethod
    def _render(value, slots):
        if not isinstance(value, str):
            return value

        def replace(match):
            name = match.group(1)
            if name not in slots:
                raise ActionPlanError(f"필수 슬롯 '{name}' 값이 없습니다.")
            return str(slots[name])

        return PLACEHOLDER_RE.sub(replace, value)

    @classmethod
    def _render_nested(cls, value, slots):
        if isinstance(value, dict):
            return {
                str(key): cls._render_nested(item, slots)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._render_nested(item, slots) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._render_nested(item, slots) for item in value)
        return cls._render(value, slots) if isinstance(value, str) else value

    @staticmethod
    def _nested_placeholders(value):
        if isinstance(value, dict):
            found = set()
            for item in value.values():
                found.update(ActionExecutor._nested_placeholders(item))
            return found
        if isinstance(value, (list, tuple)):
            found = set()
            for item in value:
                found.update(ActionExecutor._nested_placeholders(item))
            return found
        return set(PLACEHOLDER_RE.findall(value)) if isinstance(value, str) else set()

    def validate_plan(self, plan, slot_names=None):
        if not isinstance(plan, list) or not plan:
            return "행동 계획이 비어 있습니다."
        if len(plan) > 12:
            return "행동 계획은 최대 12단계까지 실행할 수 있습니다."
        allowed_slots = set(slot_names or [])

        for index, step in enumerate(plan, start=1):
            prefix = f"{index}번 단계"
            if not isinstance(step, dict):
                return f"{prefix}가 객체 형식이 아닙니다."
            action = step.get("action")
            if action not in ALLOWED_ACTIONS:
                return f"{prefix}의 action '{action}'은 지원하지 않습니다."
            for field in ("target", "direction", "text"):
                value = step.get(field, "")
                if not isinstance(value, str):
                    return f"{prefix}의 {field} 값이 문자열이 아닙니다."
                unknown = set(PLACEHOLDER_RE.findall(value)) - allowed_slots
                if unknown:
                    return f"{prefix}에 등록되지 않은 슬롯이 있습니다: {', '.join(sorted(unknown))}"

            if "selector" in step:
                selector_value = step.get("selector")
                if not isinstance(selector_value, dict):
                    return f"{prefix}의 selector는 객체 형식이어야 합니다."
                unknown_fields = sorted(set(selector_value) - SELECTOR_FIELDS)
                if unknown_fields:
                    return (
                        f"{prefix}의 selector에 지원하지 않는 필드가 있습니다: "
                        f"{', '.join(unknown_fields)}"
                    )
                for field, value in selector_value.items():
                    if not isinstance(value, str):
                        return f"{prefix}의 selector.{field}는 문자열이어야 합니다."
                unknown = self._nested_placeholders(selector_value) - allowed_slots
                if unknown:
                    return (
                        f"{prefix}의 selector에 등록되지 않은 슬롯이 있습니다: "
                        f"{', '.join(sorted(unknown))}"
                    )
                match_mode = str(
                    selector_value.get("match_mode") or "auto"
                ).casefold()
                if match_mode not in MATCH_MODES:
                    return (
                        f"{prefix}의 selector.match_mode는 "
                        "auto/exact/normalized/contains 중 하나여야 합니다."
                    )

            for field in action_spec(action).get("required", ()):
                if not step.get(field):
                    return f"{prefix}의 {field} 값이 비어 있습니다."

            if action == "app_command":
                if not isinstance(step.get("operation"), str):
                    return f"{prefix}의 operation은 문자열이어야 합니다."
                if not isinstance(step.get("params"), dict):
                    return f"{prefix}의 params는 객체 형식이어야 합니다."
                unknown = self._nested_placeholders(step.get("params")) - allowed_slots
                if unknown:
                    return (
                        f"{prefix}의 params에 등록되지 않은 슬롯이 있습니다: "
                        f"{', '.join(sorted(unknown))}"
                    )

            if "overwrite" in step and not isinstance(step.get("overwrite"), bool):
                return f"{prefix}의 overwrite 값은 true 또는 false여야 합니다."

            if action in {"open_app", "focus_window", "move_window", "window_state", "navigate_url"} and not step.get("target"):
                return f"{prefix}의 target이 비어 있습니다."
            if action == "move_window" and not step.get("direction"):
                width = step.get("width", 0)
                height = step.get("height", 0)
                if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                    return f"{prefix}에는 direction 또는 양수 width/height가 필요합니다."
            if action == "window_state" and not step.get("direction"):
                return f"{prefix}의 창 상태가 비어 있습니다."
            if action == "hotkey":
                keys = step.get("keys")
                if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key.strip() for key in keys):
                    return f"{prefix}의 keys가 올바르지 않습니다."
                try:
                    for key in keys:
                        if not PLACEHOLDER_RE.search(key):
                            resolve_key(key)
                except ValueError as error:
                    return f"{prefix}: {error}"
            if action == "type_text" and not step.get("text"):
                return f"{prefix}의 text가 비어 있습니다."
            if action == "uia_click":
                selector = step.get("selector")
                has_structured_locator = isinstance(selector, dict) and any(
                    str(selector.get(field) or "").strip()
                    for field in (
                        "automation_id", "name", "control_type",
                        "parent_name", "ancestor_name",
                    )
                )
                if (
                    not str(step.get("direction") or "").strip()
                    and not has_structured_locator
                ):
                    return (
                        f"{prefix}에는 direction 또는 식별 정보가 있는 selector가 "
                        "필요합니다."
                    )
            if action == "wait":
                seconds = step.get("seconds", 0)
                if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds <= 5:
                    return f"{prefix}의 대기 시간은 0~5초여야 합니다."
        return None

    def _render_plan(self, plan, slots):
        rendered = []
        for step in plan:
            item = dict(step)
            for field in ("target", "direction", "text"):
                item[field] = self._render(item.get(field, ""), slots)
            if "selector" in item:
                item["selector"] = self._render_nested(
                    item.get("selector", {}), slots
                )
            item["keys"] = [self._render(key, slots) for key in item.get("keys", [])]
            if item.get("action") == "app_command":
                item["operation"] = self._render(item.get("operation", ""), slots)
                item["params"] = self._render_nested(item.get("params", {}), slots)
            rendered.append(item)
        return rendered

    def _resolve_registered_target(self, target):
        resolved = self._noun_index().get(str(target).strip().lower())
        if not resolved:
            raise ActionPlanError(f"사전에 등록된 앱 또는 사이트가 아닙니다: {target}")
        return resolved

    def _find_window(self, target):
        noun, path = self._resolve_registered_target(target)
        if str(path).lower().startswith(("http://", "https://")):
            raise ActionPlanError("웹사이트에는 창 제어를 적용할 수 없습니다.")
        executable = os.path.basename(str(path).strip('"')).lower()
        executable = executable.replace(".lnk", "").replace(".exe", "")
        matching_pids = set()
        for process in psutil.process_iter(["pid", "name"]):
            try:
                process_name = (process.info.get("name") or "").lower().replace(".exe", "")
                if executable and (process_name == executable or executable in process_name):
                    matching_pids.add(process.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        windows = []
        target_lower = str(noun).lower()

        def callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd).lower()
            if pid in matching_pids or (target_lower and target_lower in title):
                windows.append(hwnd)

        win32gui.EnumWindows(callback, None)
        if not windows:
            raise ActionTargetNotFoundError(
                f"실행 중인 창을 찾지 못했습니다: {target}"
            )
        return windows[0]

    def _open_app(self, target):
        _, path = self._resolve_registered_target(target)
        os.startfile(str(path).strip('"'))

    def _focus_window(self, target):
        hwnd = self._find_window(target)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        if win32gui.GetForegroundWindow() == hwnd:
            return hwnd

        # Windows may reject SetForegroundWindow even when the request was
        # initiated by the user. Try the normal API first, then temporarily
        # join the relevant input queues and verify the final foreground HWND.
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            return hwnd

        current_thread = win32api.GetCurrentThreadId()
        foreground_hwnd = win32gui.GetForegroundWindow()
        foreground_thread = (
            win32process.GetWindowThreadProcessId(foreground_hwnd)[0]
            if foreground_hwnd else 0
        )
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached_threads = []
        try:
            for thread_id in (foreground_thread, target_thread):
                if thread_id and thread_id != current_thread and thread_id not in attached_threads:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
            try:
                win32gui.BringWindowToTop(hwnd)
            except Exception:
                pass
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            try:
                win32gui.SetActiveWindow(hwnd)
                win32gui.SetFocus(hwnd)
            except Exception:
                pass
        finally:
            for thread_id in reversed(attached_threads):
                try:
                    win32process.AttachThreadInput(
                        current_thread, thread_id, False
                    )
                except Exception:
                    pass

        if win32gui.GetForegroundWindow() != hwnd:
            # A short Alt press is the final documented user-input style
            # nudge. Never continue to keyboard input unless HWND verification
            # succeeds afterward.
            try:
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32api.keybd_event(
                        win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0
                    )
            except Exception:
                pass

        if win32gui.GetForegroundWindow() != hwnd:
            try:
                win32gui.FlashWindow(hwnd, True)
            except Exception:
                pass
            raise ActionPlanVerificationError(
                f"Windows가 대상 창의 전면 전환을 허용하지 않았습니다: {target}"
            )
        return hwnd

    def _ensure_input_target(self, target):
        """Guarantee the intended app owns keyboard input before sending keys."""
        if not str(target or "").strip():
            return
        hwnd = self._find_window(target)
        if win32gui.GetForegroundWindow() != hwnd:
            self._focus_window(target)
        if win32gui.GetForegroundWindow() != hwnd:
            raise ActionPlanVerificationError(
                f"입력 대상 창을 활성화하지 못했습니다: {target}"
            )

    def _move_window(self, target, direction, x, y, width, height):
        hwnd = self._find_window(target)
        x, y, width, height = self._expected_window_rect(direction, x, y, width, height)
        win32gui.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True)
        return hwnd

    @staticmethod
    def _expected_window_rect(direction, x, y, width, height):
        screen_width = win32api.GetSystemMetrics(0)
        screen_height = win32api.GetSystemMetrics(1)
        normalized = MOVE_DIRECTIONS.get(str(direction).strip().lower())
        if normalized == "left_half":
            x, y, width, height = 0, 0, screen_width // 2, screen_height
        elif normalized == "right_half":
            x, y, width, height = screen_width // 2, 0, screen_width // 2, screen_height
        elif normalized == "top_half":
            x, y, width, height = 0, 0, screen_width, screen_height // 2
        elif normalized == "bottom_half":
            x, y, width, height = 0, screen_height // 2, screen_width, screen_height // 2
        elif normalized == "center":
            width = width if width > 0 else screen_width * 2 // 3
            height = height if height > 0 else screen_height * 2 // 3
            x, y = (screen_width - width) // 2, (screen_height - height) // 2
        elif direction:
            raise ActionPlanError(f"지원하지 않는 창 이동 방향입니다: {direction}")
        return int(x), int(y), int(width), int(height)

    def _window_state(self, target, state):
        hwnd = self._find_window(target)
        normalized = WINDOW_STATES.get(str(state).strip().lower())
        commands = {
            "maximize": win32con.SW_MAXIMIZE,
            "minimize": win32con.SW_MINIMIZE,
            "restore": win32con.SW_RESTORE,
        }
        if normalized not in commands:
            raise ActionPlanError(f"지원하지 않는 창 상태입니다: {state}")
        win32gui.ShowWindow(hwnd, commands[normalized])

    def _verify_step(self, step):
        action = step["action"]
        if action == "wait":
            return {"status": "verified", "reason": "대기 단계 완료"}
        if action in {"hotkey", "type_text", "navigate_url"}:
            return {
                "status": "confirmation_required",
                "reason": "대상 프로그램의 결과를 자동으로 읽을 수 없는 동작",
            }
        if action == "open_app":
            _, path = self._resolve_registered_target(step["target"])
            if str(path).lower().startswith(("http://", "https://")):
                return {
                    "status": "confirmation_required",
                    "reason": "웹페이지 표시 결과는 사용자 확인 필요",
                }
            deadline = time.monotonic() + self.verification_timeout
            while True:
                if self.controller:
                    self.controller.check_cancelled()
                try:
                    self._find_window(step["target"])
                    return {"status": "verified", "reason": "실행된 창 확인"}
                except ActionPlanError:
                    if time.monotonic() >= deadline:
                        return {"status": "failed", "reason": "실행된 창을 확인하지 못했습니다."}
                    if self.controller:
                        self.controller.wait(0.1)
                    else:
                        time.sleep(0.1)
        if action == "focus_window":
            hwnd = self._find_window(step["target"])
            if win32gui.GetForegroundWindow() == hwnd:
                return {"status": "verified", "reason": "활성 창 확인"}
            return {"status": "failed", "reason": "대상 창이 활성 창이 아닙니다."}
        if action == "move_window":
            hwnd = self._find_window(step["target"])
            x, y, width, height = self._expected_window_rect(
                step["direction"], step.get("x", 0), step.get("y", 0),
                step.get("width", 0), step.get("height", 0),
            )
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            actual = (left, top, right - left, bottom - top)
            expected = (x, y, width, height)
            if all(abs(current - wanted) <= 3 for current, wanted in zip(actual, expected)):
                return {"status": "verified", "reason": "창 위치와 크기 확인"}
            return {
                "status": "failed",
                "reason": f"창 배치 불일치 (예상 {expected}, 실제 {actual})",
            }
        if action == "window_state":
            hwnd = self._find_window(step["target"])
            state = WINDOW_STATES.get(str(step["direction"]).strip().lower())
            matched = (
                state == "maximize" and bool(win32gui.IsZoomed(hwnd))
                or state == "minimize" and bool(win32gui.IsIconic(hwnd))
                or state == "restore" and not win32gui.IsZoomed(hwnd) and not win32gui.IsIconic(hwnd)
            )
            if matched:
                return {"status": "verified", "reason": f"창 상태 {state} 확인"}
            return {"status": "failed", "reason": f"창 상태 {state} 적용을 확인하지 못했습니다."}
        if action == "clipboard_set":
            matched = self._clipboard_get() == step.get("text", "")
            return {
                "status": "verified" if matched else "failed",
                "reason": "클립보드 텍스트 확인" if matched else "클립보드 내용 불일치",
            }
        if action == "clipboard_get":
            return {"status": "verified", "reason": "클립보드 읽기 완료"}
        if action in {"copy_file", "move_file"}:
            source = self._normalized_file_path(step.get("target", ""))
            destination = self._normalized_file_path(step.get("text", ""))
            if os.path.isdir(destination):
                destination = os.path.join(destination, os.path.basename(source))
            exists = os.path.isfile(destination)
            if action == "move_file":
                exists = exists and not os.path.exists(source)
            return {
                "status": "verified" if exists else "failed",
                "reason": "파일 작업 결과 확인" if exists else "대상 파일을 확인하지 못했습니다.",
            }
        if action == "create_folder":
            exists = os.path.isdir(self._normalized_file_path(step.get("target", "")))
            return {
                "status": "verified" if exists else "failed",
                "reason": "폴더 생성 확인" if exists else "생성된 폴더를 찾지 못했습니다.",
            }
        if action == "write_text_file":
            path = self._normalized_file_path(step.get("target", ""))
            try:
                with open(path, "r", encoding="utf-8") as file:
                    matched = file.read() == step.get("text", "")
            except OSError:
                matched = False
            return {
                "status": "verified" if matched else "failed",
                "reason": "텍스트 파일 내용 확인" if matched else "텍스트 파일 내용 불일치",
            }
        if action in {"uia_click", "uia_set_text", "uia_select_file"}:
            runtime = step.get("_runtime_verification", {})
            return {
                "status": runtime.get("status", "confirmation_required"),
                "reason": "UI Automation 실행 결과 확인",
            }
        if action == "app_command":
            runtime = step.get("_runtime_verification", {})
            verified = bool(runtime.get("success") and runtime.get("verified"))
            return {
                "status": "verified" if verified else "failed",
                "reason": (
                    "네이티브 앱 데이터 재조회 확인"
                    if verified else "네이티브 앱 작업 결과를 확인하지 못했습니다."
                ),
            }
        return {"status": "confirmation_required", "reason": "자동 검증 규칙 없음"}

    def _input_delivery_wait(self, seconds):
        if self.controller:
            self.controller.wait(seconds)
        else:
            time.sleep(seconds)

    def _type_text(self, text):
        text = str(text)
        previous = None
        had_previous_text = False
        opened = False
        try:
            win32clipboard.OpenClipboard()
            opened = True
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                had_previous_text = True
                previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            if opened:
                win32clipboard.CloseClipboard()

        try:
            # Give the newly focused editor time to establish its input queue,
            # then keep our clipboard value alive until WM_PASTE is consumed.
            self._input_delivery_wait(0.15)
            press_hotkey(["ctrl", "v"])
            self._input_delivery_wait(0.35)
        finally:
            # Restore only while the clipboard still contains our temporary
            # value. Do not overwrite a clipboard change made by the user in
            # the meantime.
            restore_opened = False
            try:
                win32clipboard.OpenClipboard()
                restore_opened = True
                current = (
                    win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    if win32clipboard.IsClipboardFormatAvailable(
                        win32con.CF_UNICODETEXT
                    ) else None
                )
                if current == text:
                    win32clipboard.EmptyClipboard()
                    if had_previous_text:
                        win32clipboard.SetClipboardText(
                            previous, win32con.CF_UNICODETEXT
                        )
            finally:
                if restore_opened:
                    win32clipboard.CloseClipboard()

    @staticmethod
    def _clipboard_set(text):
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
    def _clipboard_get():
        opened = False
        try:
            win32clipboard.OpenClipboard()
            opened = True
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
            return ""
        finally:
            if opened:
                win32clipboard.CloseClipboard()

    @staticmethod
    def _normalized_file_path(value):
        return os.path.abspath(os.path.expandvars(os.path.expanduser(str(value))))

    def _preflight_file_action(self, step, planned_files=None, planned_dirs=None):
        planned_files = planned_files if planned_files is not None else set()
        planned_dirs = planned_dirs if planned_dirs is not None else set()
        action = step["action"]
        target = self._normalized_file_path(step.get("target", ""))
        destination = self._normalized_file_path(step.get("text", "")) if step.get("text") else ""
        overwrite = bool(step.get("overwrite", False))
        if action in {"copy_file", "move_file"}:
            if not os.path.isfile(target) and target not in planned_files:
                raise ActionPlanError(f"원본 파일을 찾을 수 없습니다: {target}")
            parent = destination if os.path.isdir(destination) else os.path.dirname(destination)
            if not os.path.isdir(parent) and parent not in planned_dirs:
                raise ActionPlanError(f"대상 폴더를 찾을 수 없습니다: {parent}")
            final_destination = (
                os.path.join(destination, os.path.basename(target))
                if os.path.isdir(destination) or destination in planned_dirs else destination
            )
            if os.path.normcase(target) == os.path.normcase(final_destination):
                raise ActionPlanError("원본 파일과 대상 파일이 같습니다.")
            if (
                os.path.exists(final_destination)
                or final_destination in planned_files
            ) and not overwrite:
                raise ActionConfirmationRequired(
                    f"대상 파일이 이미 있습니다. 덮어쓰려면 명령에 "
                    f"'덮어써'를 명시하세요: {final_destination}",
                    action=action,
                    target=final_destination,
                )
            planned_files.add(final_destination)
            if action == "move_file":
                planned_files.discard(target)
        elif action == "write_text_file":
            parent = os.path.dirname(target)
            if not os.path.isdir(parent) and parent not in planned_dirs:
                raise ActionPlanError(f"대상 폴더를 찾을 수 없습니다: {parent}")
            if (os.path.exists(target) or target in planned_files) and not overwrite:
                raise ActionConfirmationRequired(
                    f"대상 파일이 이미 있습니다. 덮어쓰려면 명령에 "
                    f"'덮어써'를 명시하세요: {target}",
                    action=action,
                    target=target,
                )
            planned_files.add(target)
        elif action == "create_folder":
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent) and parent not in planned_dirs:
                raise ActionPlanError(f"상위 폴더를 찾을 수 없습니다: {parent}")
            planned_dirs.add(target)

    @staticmethod
    def _copy_file_exact(source, destination, overwrite=False):
        parent = os.path.dirname(destination)
        if not overwrite:
            created = False
            try:
                with open(source, "rb") as source_file, open(destination, "xb") as target_file:
                    created = True
                    shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
                    target_file.flush()
                    os.fsync(target_file.fileno())
                shutil.copystat(source, destination)
                return destination
            except FileExistsError as error:
                raise ActionConfirmationRequired(
                    f"대상 파일이 이미 있습니다: {destination}",
                    action="copy_file", target=destination,
                ) from error
            except Exception:
                if created:
                    try:
                        os.remove(destination)
                    except OSError:
                        pass
                raise

        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination)}.",
            suffix=".tmp",
            dir=parent,
        )
        os.close(descriptor)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            return destination
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    @classmethod
    def _move_file_exact(cls, source, destination, overwrite=False):
        try:
            if overwrite:
                os.replace(source, destination)
            else:
                os.rename(source, destination)
            return destination
        except FileExistsError as error:
            raise ActionConfirmationRequired(
                f"대상 파일이 이미 있습니다: {destination}",
                action="move_file", target=destination,
            ) from error
        except OSError:
            # Cross-volume move: copy safely first and remove the source only
            # after the destination has been flushed/replaced successfully.
            cls._copy_file_exact(source, destination, overwrite=overwrite)
            os.remove(source)
            return destination

    @staticmethod
    def _write_text_exact(destination, text, overwrite=False):
        if not overwrite:
            try:
                with open(destination, "x", encoding="utf-8") as file:
                    file.write(str(text))
                    file.flush()
                    os.fsync(file.fileno())
                return destination
            except FileExistsError as error:
                raise ActionConfirmationRequired(
                    f"대상 파일이 이미 있습니다: {destination}",
                    action="write_text_file", target=destination,
                ) from error

        parent = os.path.dirname(destination)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination)}.",
            suffix=".tmp",
            dir=parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(str(text))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            return destination
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    def _execute_file_action(self, step):
        action = step["action"]
        target = self._normalized_file_path(step.get("target", ""))
        destination = self._normalized_file_path(step.get("text", "")) if step.get("text") else ""
        overwrite = bool(step.get("overwrite", False))
        if action == "copy_file":
            final = (
                os.path.join(destination, os.path.basename(target))
                if os.path.isdir(destination) else destination
            )
            return self._copy_file_exact(target, final, overwrite)
        if action == "move_file":
            final = (
                os.path.join(destination, os.path.basename(target))
                if os.path.isdir(destination) else destination
            )
            return self._move_file_exact(target, final, overwrite)
        if action == "create_folder":
            os.makedirs(target, exist_ok=True)
            return target
        if action == "write_text_file":
            return self._write_text_exact(
                target, step.get("text", ""), overwrite
            )
        return ""

    def _execute_rendered_step(self, step, index, step_results):
        action = step["action"]
        if action == "open_app":
            self._open_app(step["target"])
        elif action == "focus_window":
            self._focus_window(step["target"])
        elif action == "move_window":
            self._move_window(
                step["target"], step["direction"], step.get("x", 0), step.get("y", 0),
                step.get("width", 0), step.get("height", 0),
            )
        elif action == "window_state":
            self._window_state(step["target"], step["direction"])
        elif action == "hotkey":
            self._ensure_input_target(step.get("target"))
            press_hotkey([key.strip().lower() for key in step["keys"]])
        elif action == "type_text":
            self._ensure_input_target(step.get("target"))
            self._type_text(step["text"])
        elif action == "wait":
            if self.controller:
                self.controller.wait(step["seconds"])
            else:
                time.sleep(step["seconds"])
        elif action == "navigate_url":
            os.startfile(step["target"])
        elif action == "clipboard_set":
            self._clipboard_set(step["text"])
        elif action == "clipboard_get":
            step_results.append({"step": index, "action": action, "output": self._clipboard_get()})
        elif action in {"copy_file", "move_file", "create_folder", "write_text_file"}:
            output_path = self._execute_file_action(step)
            step_results.append({"step": index, "action": action, "output": output_path})
        elif action == "uia_click":
            locator = step.get("selector") or step["direction"]
            runtime = self.ui_automation.click(step["target"], locator)
            step["_runtime_verification"] = runtime
            step_results.append({"step": index, "action": action, "output": runtime})
        elif action == "uia_set_text":
            locator = step.get("selector") or step.get("direction", "")
            runtime = self.ui_automation.set_text(
                step["target"], locator, step["text"]
            )
            step["_runtime_verification"] = runtime
            step_results.append({"step": index, "action": action, "output": runtime})
        elif action == "uia_select_file":
            runtime = self.ui_automation.select_explorer_file(step["target"])
            step["_runtime_verification"] = runtime
            step_results.append({"step": index, "action": action, "output": runtime})
        elif action == "app_command":
            if self.app_action_registry is None:
                raise ActionPlanError("네이티브 앱 작업 실행기가 연결되지 않았습니다.")
            prepared = step.get("_prepared_action")
            if not isinstance(prepared, PreparedAction):
                raise ActionPlanError(
                    "app_command는 실행 전에 실제 앱 상태 조사와 승인이 필요합니다."
                )
            runtime = self.app_action_registry.execute(prepared)
            step["_runtime_verification"] = runtime
            step_results.append({"step": index, "action": action, "output": runtime})

    def execute_plan(
        self, plan, slots=None, log_callback=None, start_step=1, retry_attempts=None,
        prepared_app_actions=None,
    ):
        self.ui_automation.noun_dict = self.noun_dict
        self.ui_automation.begin_command()
        started = time.monotonic()
        slots = dict(slots or {})
        issue = self.validate_plan(plan, slot_names=slots.keys())
        if issue:
            raise ActionPlanError(issue)
        rendered = self._render_plan(plan, slots)
        if not isinstance(start_step, int) or start_step < 1 or start_step > len(rendered):
            raise ActionPlanError("재시작 단계 번호가 행동 계획 범위를 벗어났습니다.")
        if retry_attempts is not None:
            retry_attempts = max(0, min(int(retry_attempts or 0), 2))
        active_steps = rendered[start_step - 1:]
        # Preflight rendered targets before any external action begins.
        planned_files = set()
        planned_dirs = set()
        for offset, step in enumerate(active_steps, start=start_step):
            if step["action"] in app_target_actions():
                self._resolve_registered_target(step["target"])
            elif step["action"] in {"hotkey", "type_text"} and step.get("target"):
                self._resolve_registered_target(step["target"])
            elif step["action"] == "navigate_url" and not step["target"].lower().startswith(("http://", "https://")):
                raise ActionPlanError("URL은 http:// 또는 https:// 형식이어야 합니다.")
            elif step["action"] in {"copy_file", "move_file", "create_folder", "write_text_file"}:
                self._preflight_file_action(step, planned_files, planned_dirs)
            elif step["action"] == "uia_select_file":
                file_path = self._normalized_file_path(step.get("target", ""))
                if not os.path.isfile(file_path):
                    raise ActionPlanError(f"선택할 파일을 찾을 수 없습니다: {file_path}")
            elif step["action"] == "app_command":
                prepared_value = None
                if isinstance(prepared_app_actions, dict):
                    prepared_value = prepared_app_actions.get(
                        offset, prepared_app_actions.get(str(offset))
                    )
                elif isinstance(prepared_app_actions, (list, tuple)):
                    prepared_index = offset - 1
                    if prepared_index < len(prepared_app_actions):
                        prepared_value = prepared_app_actions[prepared_index]
                if isinstance(prepared_value, dict):
                    prepared_value = PreparedAction.from_dict(prepared_value)
                if not isinstance(prepared_value, PreparedAction):
                    raise ActionPlanError(
                        "app_command는 첫 외부 변경 전에 준비와 승인을 완료해야 합니다."
                    )
                if (
                    prepared_value.app != str(step.get("target", "")).casefold()
                    or prepared_value.operation != step.get("operation")
                ):
                    raise ActionPlanError(
                        "준비된 앱 작업이 행동 계획의 대상 또는 작업과 일치하지 않습니다."
                    )
                step["_prepared_action"] = prepared_value

        completed = []
        verification = []
        step_results = []
        for index, step in enumerate(active_steps, start=start_step):
            action = step["action"]
            if log_callback:
                log_callback(f"[ActionPlan] {index}/{len(rendered)} {action}")
            attempt = 1
            while True:
                try:
                    if self.controller:
                        self.controller.check_cancelled()
                        self.controller.event(action, "running", {"step": index, "attempt": attempt})
                    self._execute_rendered_step(step, index, step_results)
                    checked = self._verify_step(step)
                    checked.update({"step": index, "action": action, "attempt": attempt})
                    if checked["status"] == "failed":
                        raise ActionPlanVerificationError(
                            f"{index}번 단계 결과 확인 실패: {checked['reason']}",
                            completed=completed + [action],
                            verification=verification + [checked],
                        )
                    completed.append(action)
                    verification.append(checked)
                    if self.controller:
                        self.controller.event(action, "success", {"step": index, "attempt": attempt})
                    break
                except ExecutionCancelled:
                    if self.controller:
                        self.controller.event(action, "cancelled", {"step": index})
                    raise
                except Exception as error:
                    error_type = classify_action_error(error)
                    allowed_retries = retry_limit(
                        action, error_type, override=retry_attempts
                    )
                    if attempt <= allowed_retries:
                        if log_callback:
                            log_callback(
                                f"[ActionPlan] {index}단계 재시도 "
                                f"{attempt}/{allowed_retries}: {error}"
                            )
                        if self.controller:
                            self.controller.event(action, "retry", {
                                "step": index,
                                "attempt": attempt,
                                "max_retries": allowed_retries,
                                "error": str(error),
                                "error_type": error_type,
                            })
                            self.controller.wait(0.1)
                        else:
                            time.sleep(0.1)
                        attempt += 1
                        continue
                    try:
                        error.failed_step = index
                        error.retryable = bool(
                            action_spec(action).get("retryable")
                        )
                        error.error_type = error_type
                    except Exception:
                        pass
                    if self.controller:
                        self.controller.event(action, "failed", {
                            "step": index,
                            "attempt": attempt,
                            "error": str(error),
                            "error_type": error_type,
                        })
                    raise

        overall = (
            "confirmation_required"
            if any(item["status"] == "confirmation_required" for item in verification)
            else "verified"
        )
        return {
            "success": True,
            "message": "행동 계획을 실행했습니다.",
            "response": "행동 계획을 실행했습니다.",
            "verified": overall == "verified",
            "error_type": None,
            "failed_step": None,
            "retryable": False,
            "target": None,
            "completed": completed,
            "slots": slots,
            "verification_status": overall,
            "verification": verification,
            "step_results": step_results,
            "status": "success",
            "action": "action_plan",
            "start_step": start_step,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "data": {
                "completed": completed,
                "verification": verification,
                "step_results": step_results,
            },
        }
