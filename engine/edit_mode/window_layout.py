"""Optional, failure-isolated document/Jarvis side-by-side layout."""

from __future__ import annotations

import threading
import time


class WindowLayoutError(RuntimeError):
    pass


class Win32WindowBackend:
    JARVIS_TITLE = "Jarvis ⚡ Command Center"

    @staticmethod
    def is_window(handle: int) -> bool:
        import win32gui

        return bool(handle and win32gui.IsWindow(int(handle)))

    def find_jarvis_window(self, excluded_handle=0) -> int:
        import win32gui

        matches = []

        def callback(handle, _):
            if int(handle) == int(excluded_handle or 0):
                return
            if not win32gui.IsWindowVisible(handle):
                return
            title = str(win32gui.GetWindowText(handle) or "")
            if self.JARVIS_TITLE.casefold() in title.casefold():
                matches.append(int(handle))

        win32gui.EnumWindows(callback, None)
        return matches[0] if len(matches) == 1 else 0

    @staticmethod
    def work_area(handle: int) -> tuple[int, int, int, int]:
        import win32api

        monitor = win32api.MonitorFromWindow(int(handle), 2)
        area = win32api.GetMonitorInfo(monitor)["Work"]
        return tuple(int(value) for value in area)

    @staticmethod
    def restore(handle: int) -> None:
        import win32con
        import win32gui

        win32gui.ShowWindow(int(handle), win32con.SW_RESTORE)

    @staticmethod
    def move(handle: int, rect: tuple[int, int, int, int]) -> None:
        import win32gui

        left, top, right, bottom = rect
        win32gui.MoveWindow(
            int(handle),
            left,
            top,
            right - left,
            bottom - top,
            True,
        )

    @staticmethod
    def rect(handle: int) -> tuple[int, int, int, int]:
        import win32gui

        return tuple(int(value) for value in win32gui.GetWindowRect(int(handle)))


class Win32DocumentActivationBackend:
    """Activate one validated top-level document window without keystrokes."""

    @staticmethod
    def root_handle(handle: int) -> int:
        import win32con
        import win32gui

        value = int(handle or 0)
        if not value or not win32gui.IsWindow(value):
            return value
        return int(win32gui.GetAncestor(value, win32con.GA_ROOT) or value)

    @staticmethod
    def is_window(handle: int) -> bool:
        import win32gui

        return bool(
            handle
            and win32gui.IsWindow(int(handle))
            and win32gui.IsWindowVisible(int(handle))
        )

    @staticmethod
    def foreground_root() -> int:
        import win32con
        import win32gui

        foreground = int(win32gui.GetForegroundWindow() or 0)
        if not foreground:
            return 0
        return int(win32gui.GetAncestor(foreground, win32con.GA_ROOT) or foreground)

    @staticmethod
    def restore(handle: int) -> None:
        import win32con
        import win32gui

        command = win32con.SW_RESTORE if win32gui.IsIconic(int(handle)) else win32con.SW_SHOW
        win32gui.ShowWindow(int(handle), command)

    @staticmethod
    def request_foreground(handle: int) -> None:
        import ctypes

        import win32api
        import win32con
        import win32gui
        import win32process

        target = int(handle)
        foreground = int(win32gui.GetForegroundWindow() or 0)
        caller_thread = int(win32api.GetCurrentThreadId())
        target_thread = int(win32process.GetWindowThreadProcessId(target)[0] or 0)
        foreground_thread = (
            int(win32process.GetWindowThreadProcessId(foreground)[0] or 0)
            if foreground
            else 0
        )
        attached = []
        try:
            for thread_id in {target_thread, foreground_thread}:
                if not thread_id or thread_id == caller_thread:
                    continue
                if ctypes.windll.user32.AttachThreadInput(
                    caller_thread,
                    thread_id,
                    True,
                ):
                    attached.append(thread_id)
            win32gui.SetWindowPos(
                target,
                win32con.HWND_TOP,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_SHOWWINDOW,
            )
            win32gui.BringWindowToTop(target)
            win32gui.SetForegroundWindow(target)
            try:
                win32gui.SetActiveWindow(target)
            except Exception:
                pass
        finally:
            for thread_id in reversed(attached):
                ctypes.windll.user32.AttachThreadInput(
                    caller_thread,
                    thread_id,
                    False,
                )


class DocumentWindowActivator:
    """Failure-isolated, verified foreground activation for connected documents."""

    def __init__(self, backend=None, attempts=2, retry_delay=0.03):
        self.backend = backend or Win32DocumentActivationBackend()
        self.attempts = max(1, min(int(attempts), 3))
        self.retry_delay = max(0.0, min(float(retry_delay), 0.1))
        self._lock = threading.RLock()

    def activate(self, document_handle: int) -> dict:
        handle = int(document_handle or 0)
        with self._lock:
            try:
                root_handle = getattr(self.backend, "root_handle", None)
                if callable(root_handle):
                    handle = int(root_handle(handle) or handle)
                if not self.backend.is_window(handle):
                    return {
                        "success": False,
                        "status": "window_unavailable",
                        "focused": False,
                    }
                if self.backend.foreground_root() == handle:
                    return {
                        "success": True,
                        "status": "already_foreground",
                        "focused": True,
                    }
                self.backend.restore(handle)
                for attempt in range(self.attempts):
                    self.backend.request_foreground(handle)
                    if self.backend.foreground_root() == handle:
                        return {
                            "success": True,
                            "status": "focused",
                            "focused": True,
                        }
                    if attempt + 1 < self.attempts and self.retry_delay:
                        time.sleep(self.retry_delay)
                return {
                    "success": False,
                    "status": "focus_rejected",
                    "focused": False,
                }
            except Exception as error:
                return {
                    "success": False,
                    "status": "focus_failed",
                    "focused": False,
                    "message": str(error),
                }

    def activate_when_ready(
        self,
        document_handle: int,
        *,
        timeout=2.5,
        retry_interval=0.1,
    ) -> dict:
        """Bounded startup wait for an exact document window to accept focus."""
        deadline = time.monotonic() + max(0.0, min(float(timeout), 5.0))
        interval = max(0.0, min(float(retry_interval), 0.25))
        attempts = 0
        last = {
            "success": False,
            "status": "window_unavailable",
            "focused": False,
        }
        while True:
            attempts += 1
            last = self.activate(document_handle)
            if last.get("success") and last.get("focused"):
                return {**last, "readiness_attempts": attempts}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {**last, "readiness_attempts": attempts}
            if interval:
                time.sleep(min(interval, remaining))


class WindowLayoutManager:
    """Arrange once per session; later user window moves are never overridden."""

    def __init__(self, backend=None, enabled=True, document_ratio=0.68):
        self.backend = backend or Win32WindowBackend()
        self._enabled = bool(enabled)
        self.document_ratio = max(0.6, min(float(document_ratio), 0.72))
        self._arranged_sessions = set()
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled) -> bool:
        with self._lock:
            self._enabled = bool(enabled)
            return self._enabled

    @staticmethod
    def _matches(actual, expected, tolerance=12) -> bool:
        return all(abs(int(left) - int(right)) <= tolerance for left, right in zip(
            actual, expected
        ))

    def arrange(self, session_id: str, document_handle: int) -> dict:
        with self._lock:
            if not self._enabled:
                return {"success": True, "status": "disabled", "arranged": False}
            if session_id in self._arranged_sessions:
                return {"success": True, "status": "already_arranged", "arranged": False}
            try:
                if not self.backend.is_window(document_handle):
                    raise WindowLayoutError("문서 창 핸들을 확인하지 못했습니다.")
                jarvis_handle = self.backend.find_jarvis_window(document_handle)
                if not self.backend.is_window(jarvis_handle):
                    raise WindowLayoutError("JARVIS 창을 하나로 식별하지 못했습니다.")
                left, top, right, bottom = self.backend.work_area(document_handle)
                width = right - left
                height = bottom - top
                if width < 1000 or height < 500:
                    raise WindowLayoutError("화면 작업 영역이 자동 배치 최소 크기보다 작습니다.")
                split = left + int(width * self.document_ratio)
                document_rect = (left, top, split, bottom)
                jarvis_rect = (split, top, right, bottom)
                if split - left < 600 or right - split < 320:
                    raise WindowLayoutError("두 창의 최소 너비를 확보할 수 없습니다.")
                self.backend.restore(document_handle)
                self.backend.restore(jarvis_handle)
                self.backend.move(document_handle, document_rect)
                self.backend.move(jarvis_handle, jarvis_rect)
                if not self._matches(self.backend.rect(document_handle), document_rect):
                    raise WindowLayoutError("문서 창 배치 결과를 확인하지 못했습니다.")
                if not self._matches(self.backend.rect(jarvis_handle), jarvis_rect):
                    raise WindowLayoutError("JARVIS 창 배치 결과를 확인하지 못했습니다.")
                self._arranged_sessions.add(str(session_id))
                return {
                    "success": True,
                    "status": "arranged",
                    "arranged": True,
                    "document_ratio": self.document_ratio,
                }
            except Exception as error:
                return {
                    "success": False,
                    "status": "skipped",
                    "arranged": False,
                    "message": str(error),
                }
