"""Non-activating visual marker for the connected Excel selection."""

from __future__ import annotations

import atexit
import os
import queue
import re
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Callable

_CELL_RANGE = re.compile(
    r"^\$?(?P<first_col>[A-Z]{1,3})\$?(?P<first_row>[1-9]\d{0,6})"
    r"(?::\$?(?P<last_col>[A-Z]{1,3})\$?(?P<last_row>[1-9]\d{0,6}))?$"
)
_MAX_EXCEL_COLUMN = 16384
_MAX_EXCEL_ROW = 1_048_576


def _column_number(letters: str) -> int:
    value = 0
    for character in str(letters or "").upper():
        value = value * 26 + ord(character) - 64
    return value


def normalized_excel_range(value: str) -> tuple[str, str] | None:
    """Return the two corner addresses for one valid Excel rectangle."""
    text = str(value or "").strip().upper()
    match = _CELL_RANGE.fullmatch(text)
    if not match:
        return None
    first_col = match.group("first_col")
    last_col = match.group("last_col") or first_col
    first_row = int(match.group("first_row"))
    last_row = int(match.group("last_row") or first_row)
    if (
        not 1 <= _column_number(first_col) <= _MAX_EXCEL_COLUMN
        or not 1 <= _column_number(last_col) <= _MAX_EXCEL_COLUMN
        or not 1 <= first_row <= _MAX_EXCEL_ROW
        or not 1 <= last_row <= _MAX_EXCEL_ROW
    ):
        return None
    return f"{first_col}{first_row}", f"{last_col}{last_row}"


@dataclass(frozen=True)
class ScreenRectangle:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def visible(self) -> bool:
        return self.width >= 2 and self.height >= 2


class Win32ExcelWindowApi:
    @staticmethod
    def usable(handle: int) -> bool:
        import win32gui

        return bool(
            handle
            and win32gui.IsWindow(int(handle))
            and win32gui.IsWindowVisible(int(handle))
            and not win32gui.IsIconic(int(handle))
            and str(win32gui.GetClassName(int(handle)) or "") == "XLMAIN"
        )

    @staticmethod
    def rectangle(handle: int) -> ScreenRectangle:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(int(handle))
        return ScreenRectangle(int(left), int(top), int(right), int(bottom))


class ExcelComSelectionLocator:
    """Use Excel's document geometry for the common unsplit-window case."""

    def __init__(
        self,
        application_factory=None,
        window_api=None,
        com_runtime=None,
        dpi_getter=None,
    ):
        self._application_factory = application_factory
        self._window_api = window_api or Win32ExcelWindowApi()
        self._com_runtime = com_runtime
        self._dpi_getter = dpi_getter

    def _application(
        self,
        expected_path=None,
        window_handle=0,
        runtime_document_id=None,
        document_name=None,
    ):
        if self._application_factory is not None:
            return self._application_factory()
        if expected_path or runtime_document_id:
            from engine.edit_mode.native_bridge import (
                excel_reference_for_identity,
            )

            application, _, _ = excel_reference_for_identity(
                expected_path=str(expected_path or ""),
                window_handle=int(window_handle or 0),
                runtime_document_id=str(runtime_document_id or ""),
                document_name=str(document_name or ""),
            )
            if application is not None:
                return application
        import win32com.client

        return win32com.client.GetActiveObject("Excel.Application")

    def try_locate(
        self,
        window_handle: int,
        selection_reference: str,
        expected_path: str | None = None,
        runtime_document_id: str | None = None,
        document_name: str | None = None,
    ) -> tuple[bool, ScreenRectangle | None]:
        addresses = normalized_excel_range(selection_reference)
        if not addresses or not self._window_api.usable(window_handle):
            return True, None

        runtime = self._com_runtime
        if runtime is None:
            import pythoncom

            runtime = pythoncom
        application = window = sheet = selected = visible = intersection = None
        runtime.CoInitialize()
        try:
            application = self._application(
                expected_path,
                window_handle,
                runtime_document_id,
                document_name,
            )
            window = getattr(application, "ActiveWindow", None)
            sheet = getattr(application, "ActiveSheet", None)
            if window is None or sheet is None:
                return False, None
            actual_handle = int(
                getattr(window, "Hwnd", 0)
                or getattr(window, "HWND", 0)
                or getattr(application, "Hwnd", 0)
                or 0
            )
            if actual_handle != int(window_handle):
                return False, None
            if (
                bool(getattr(window, "FreezePanes", False))
                or int(getattr(window, "SplitRow", 0) or 0)
                or int(getattr(window, "SplitColumn", 0) or 0)
            ):
                return False, None

            reference = (
                addresses[0]
                if addresses[0] == addresses[1]
                else f"{addresses[0]}:{addresses[1]}"
            )
            selected = sheet.Range(reference)
            visible = window.VisibleRange
            intersection = application.Intersect(selected, visible)
            if intersection is None:
                return True, None

            zoom = float(getattr(window, "Zoom", 100) or 100)
            if not 10 <= zoom <= 400:
                return False, None
            try:
                if self._dpi_getter is not None:
                    dpi = int(self._dpi_getter(int(window_handle)))
                else:
                    import ctypes

                    dpi = int(
                        ctypes.windll.user32.GetDpiForWindow(int(window_handle))
                    )
            except Exception:
                dpi = 96
            if dpi < 72:
                dpi = 96
            scale = (dpi / 72.0) * (zoom / 100.0)
            origin_x = int(window.PointsToScreenPixelsX(0))
            origin_y = int(window.PointsToScreenPixelsY(0))
            # The origin methods already include the current scroll offset.
            # Range geometry remains in absolute worksheet points.
            left = round(origin_x + float(intersection.Left) * scale)
            top = round(origin_y + float(intersection.Top) * scale)
            right = round(left + float(intersection.Width) * scale)
            bottom = round(top + float(intersection.Height) * scale)
            excel_window = self._window_api.rectangle(window_handle)
            result = ScreenRectangle(
                max(excel_window.left, left),
                max(excel_window.top, top),
                min(excel_window.right, right),
                min(excel_window.bottom, bottom),
            )
            return True, result if result.visible else None
        except Exception:
            # Office can reject automation while the user is typing. UIA is a
            # slower but independent fallback and runs outside the API request.
            return False, None
        finally:
            intersection = selected = visible = sheet = window = application = None
            runtime.CoUninitialize()


class ExcelSelectionLocator:
    """Locate only visible Excel cells exposed by Windows UI Automation."""

    def __init__(
        self,
        desktop_factory: Callable | None = None,
        window_api=None,
        fast_locator=None,
    ):
        self._desktop_factory = desktop_factory
        self._window_api = window_api or Win32ExcelWindowApi()
        self._fast_locator = fast_locator
        if self._fast_locator is None and desktop_factory is None:
            self._fast_locator = ExcelComSelectionLocator(
                window_api=self._window_api
            )

    def _desktop(self):
        if self._desktop_factory is not None:
            return self._desktop_factory()
        # Excel and Eel may already have initialized COM as STA on this thread.
        # pywinauto's import-time apartment warning is expected in that case and
        # should not leak into the packaged application's console/log output.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Revert to STA COM threading mode",
                category=UserWarning,
            )
            from pywinauto import Desktop

        return Desktop(backend="uia")

    @staticmethod
    def _cell_rectangle(window, address: str) -> ScreenRectangle | None:
        cell = window.child_window(
            auto_id=address,
            control_type="DataItem",
        ).wrapper_object()
        if not cell.is_visible():
            return None
        rectangle = cell.rectangle()
        result = ScreenRectangle(
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )
        return result if result.visible else None

    def locate(
        self,
        window_handle: int,
        selection_reference: str,
        expected_path: str | None = None,
        runtime_document_id: str | None = None,
        document_name: str | None = None,
    ) -> ScreenRectangle | None:
        addresses = normalized_excel_range(selection_reference)
        if not addresses or not self._window_api.usable(window_handle):
            return None
        if self._fast_locator is not None:
            handled, rectangle = self._fast_locator.try_locate(
                window_handle,
                selection_reference,
                expected_path,
                runtime_document_id,
                document_name,
            )
            if handled:
                return rectangle
        try:
            window = self._desktop().window(handle=int(window_handle))
            first = self._cell_rectangle(window, addresses[0])
            last = (
                first
                if addresses[1] == addresses[0]
                else self._cell_rectangle(window, addresses[1])
            )
            if first is None or last is None:
                return None
            excel_window = self._window_api.rectangle(window_handle)
            result = ScreenRectangle(
                max(excel_window.left, min(first.left, last.left)),
                max(excel_window.top, min(first.top, last.top)),
                min(excel_window.right, max(first.right, last.right)),
                min(excel_window.bottom, max(first.bottom, last.bottom)),
            )
            return result if result.visible else None
        except Exception:
            return None


class Win32SelectionOverlayBackend:
    """Draw one click-through owner window without activating it."""

    BORDER = 3
    LABEL_HEIGHT = 24
    COLOR_KEY = 0x000000
    ACCENT = 0x00E8A64A  # COLORREF for RGB(74, 166, 232)
    DEFAULT_GUI_FONT = 17

    def __init__(self):
        self._commands: queue.Queue = queue.Queue()
        self._thread = None
        self._lock = threading.RLock()
        self._window = 0
        self._owner = 0
        self._class_name = f"JarvisSelectionOverlay_{os.getpid()}_{id(self)}"
        self._label = "JARVIS"
        self._cell_size = (1, 1)
        self._label_width = 104
        self._closed = False
        atexit.register(self.close)

    def _ensure_thread(self):
        with self._lock:
            if self._closed:
                return False
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="jarvis-selection-overlay",
                    daemon=True,
                )
                self._thread.start()
            return True

    def show(self, owner_handle: int, rectangle: ScreenRectangle, label: str) -> bool:
        if os.name != "nt" or not rectangle.visible or not self._ensure_thread():
            return False
        self._commands.put(("show", int(owner_handle), rectangle, str(label)))
        return True

    def hide(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._commands.put(("hide",))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None and self._thread.is_alive():
                self._commands.put(("close",))

    def _paint(self, handle, message, wparam, lparam):
        import win32api
        import win32con
        import win32gui

        if message == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if message == win32con.WM_ERASEBKGND:
            return 1
        if message != win32con.WM_PAINT:
            return win32gui.DefWindowProc(handle, message, wparam, lparam)

        dc, paint = win32gui.BeginPaint(handle)
        transparent_brush = win32gui.CreateSolidBrush(self.COLOR_KEY)
        accent_brush = win32gui.CreateSolidBrush(self.ACCENT)
        old_font = None
        try:
            client = win32gui.GetClientRect(handle)
            win32gui.FillRect(dc, client, transparent_brush)
            label_rect = (0, 0, self._label_width, self.LABEL_HEIGHT)
            win32gui.FillRect(dc, label_rect, accent_brush)

            width, height = self._cell_size
            x = self.BORDER
            y = self.LABEL_HEIGHT + self.BORDER
            border = self.BORDER
            win32gui.FillRect(dc, (x, y, x + width, y + border), accent_brush)
            win32gui.FillRect(
                dc,
                (x, y + height - border, x + width, y + height),
                accent_brush,
            )
            win32gui.FillRect(dc, (x, y, x + border, y + height), accent_brush)
            win32gui.FillRect(
                dc,
                (x + width - border, y, x + width, y + height),
                accent_brush,
            )

            old_font = win32gui.SelectObject(
                dc,
                win32gui.GetStockObject(self.DEFAULT_GUI_FONT),
            )
            win32gui.SetBkMode(dc, win32con.TRANSPARENT)
            win32gui.SetTextColor(dc, win32api.RGB(255, 255, 255))
            win32gui.DrawText(
                dc,
                self._label,
                -1,
                label_rect,
                win32con.DT_CENTER
                | win32con.DT_VCENTER
                | win32con.DT_SINGLELINE
                | win32con.DT_NOPREFIX,
            )
        finally:
            if old_font is not None:
                win32gui.SelectObject(dc, old_font)
            win32gui.DeleteObject(accent_brush)
            win32gui.DeleteObject(transparent_brush)
            win32gui.EndPaint(handle, paint)
        return 0

    def _create_window(self, owner_handle: int):
        import win32api
        import win32con
        import win32gui

        if self._window and self._owner == owner_handle:
            return
        if self._window:
            win32gui.DestroyWindow(self._window)
            self._window = 0
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        window_class.lpfnWndProc = self._paint
        try:
            win32gui.RegisterClass(window_class)
        except win32gui.error as error:
            if getattr(error, "winerror", None) != 1410:
                raise
        extended_style = (
            win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
        )
        self._window = win32gui.CreateWindowEx(
            extended_style,
            self._class_name,
            "",
            win32con.WS_POPUP,
            0,
            0,
            1,
            1,
            int(owner_handle),
            0,
            instance,
            None,
        )
        self._owner = int(owner_handle)
        win32gui.SetLayeredWindowAttributes(
            self._window,
            self.COLOR_KEY,
            255,
            win32con.LWA_COLORKEY,
        )

    def _show(self, owner_handle: int, rectangle: ScreenRectangle, label: str):
        import win32con
        import win32gui

        self._create_window(owner_handle)
        self._label = label[:64]
        self._cell_size = (rectangle.width, rectangle.height)
        self._label_width = max(104, min(260, 54 + len(self._label) * 8))
        window_width = max(
            rectangle.width + self.BORDER * 2,
            self._label_width,
        )
        window_height = rectangle.height + self.LABEL_HEIGHT + self.BORDER * 2
        win32gui.SetWindowPos(
            self._window,
            win32con.HWND_TOP,
            rectangle.left - self.BORDER,
            rectangle.top - self.LABEL_HEIGHT - self.BORDER,
            window_width,
            window_height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        win32gui.InvalidateRect(self._window, None, True)
        win32gui.UpdateWindow(self._window)

    def _run(self):
        import win32con
        import win32gui

        running = True
        while running:
            try:
                command = self._commands.get(timeout=0.05)
            except queue.Empty:
                command = None
            try:
                if command:
                    if command[0] == "show":
                        self._show(command[1], command[2], command[3])
                    elif command[0] == "hide" and self._window:
                        win32gui.ShowWindow(self._window, win32con.SW_HIDE)
                    elif command[0] == "close":
                        running = False
                win32gui.PumpWaitingMessages()
            except Exception:
                if self._window:
                    try:
                        win32gui.ShowWindow(self._window, win32con.SW_HIDE)
                    except Exception:
                        pass
        if self._window:
            try:
                win32gui.DestroyWindow(self._window)
            except Exception:
                pass
            self._window = 0


class SelectionOverlayManager:
    """Failure-isolated policy for when an Excel marker may be displayed."""

    # Shorter than the GUI's 700 ms context monitor so a stationary address is
    # still relocated after scrolling, zooming, or moving the Excel window.
    CACHE_SECONDS = 0.5

    def __init__(self, locator=None, backend=None, enabled=True):
        self.locator = locator or ExcelSelectionLocator()
        self.backend = backend or Win32SelectionOverlayBackend()
        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker = None
        self._pending = None
        self._request_id = 0
        self._closed = False
        self._cache_key = None
        self._cache_rectangle = None
        self._cache_at = 0.0
        self._status = {
            "enabled": self._enabled,
            "visible": False,
            "status": "hidden",
            "selection_reference": None,
        }

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def hide(self, reason="hidden") -> dict:
        with self._condition:
            self._request_id += 1
            self._pending = None
            self._cache_key = None
            self._cache_rectangle = None
            self._cache_at = 0.0
            self.backend.hide()
            self._status = {
                "enabled": self._enabled,
                "visible": False,
                "status": str(reason or "hidden"),
                "selection_reference": None,
            }
            return dict(self._status)

    @staticmethod
    def _target(session, context):
        session_value = dict(session or {})
        context_value = dict(context or {})
        if str(session_value.get("app_type") or "").casefold() != "excel":
            return None, "unsupported_app"
        if str(context_value.get("selection_kind") or "") != "range":
            return None, "unsupported_selection"
        reference = str(
            context_value.get("selection_reference") or ""
        ).replace("$", "").upper()
        if normalized_excel_range(reference) is None:
            return None, "unsupported_selection"
        handle = int(session_value.get("window_handle") or 0)
        if not handle:
            return None, "window_unavailable"
        expected_path = str(session_value.get("file_path") or "").strip()
        runtime_document_id = str(
            session_value.get("runtime_document_id") or ""
        ).strip().upper()
        document_name = str(session_value.get("document_name") or "").strip()
        if not expected_path and not runtime_document_id:
            return None, "document_unavailable"
        return (
            handle,
            reference,
            expected_path,
            runtime_document_id,
            document_name,
        ), None

    def _apply_rectangle(
        self,
        request_id,
        handle,
        reference,
        expected_path,
        runtime_document_id,
        document_name,
        rectangle,
    ):
        with self._condition:
            if request_id != self._request_id or not self._enabled:
                return dict(self._status)
            if rectangle is None:
                self.backend.hide()
                self._status = {
                    "enabled": True,
                    "visible": False,
                    "status": "out_of_view",
                    "selection_reference": None,
                }
                return dict(self._status)
            shown = self.backend.show(
                handle,
                rectangle,
                f"JARVIS · {reference}",
            )
            if shown:
                self._cache_key = (
                    handle,
                    reference,
                    expected_path,
                    runtime_document_id,
                    document_name,
                )
                self._cache_rectangle = rectangle
                self._cache_at = time.monotonic()
            self._status = {
                "enabled": True,
                "visible": bool(shown),
                "status": "shown" if shown else "unavailable",
                "selection_reference": reference if shown else None,
            }
            return dict(self._status)

    def _ensure_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._work,
                name="jarvis-selection-locator",
                daemon=True,
            )
            self._worker.start()

    def _work(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                (
                    request_id,
                    handle,
                    reference,
                    expected_path,
                    runtime_document_id,
                    document_name,
                ) = self._pending
                self._pending = None
            rectangle = self.locator.locate(
                handle,
                reference,
                expected_path,
                runtime_document_id,
                document_name,
            )
            self._apply_rectangle(
                request_id,
                handle,
                reference,
                expected_path,
                runtime_document_id,
                document_name,
                rectangle,
            )

    def schedule(self, session, context) -> dict:
        """Queue only the latest marker lookup and return without blocking UI."""
        target, reason = self._target(session, context)
        if reason:
            return self.hide(reason)
        (
            handle,
            reference,
            expected_path,
            runtime_document_id,
            document_name,
        ) = target
        with self._condition:
            if not self._enabled:
                return self.hide("disabled")
            self._request_id += 1
            request_id = self._request_id
            cache_fresh = (
                self._cache_key == target
                and self._cache_rectangle is not None
                and time.monotonic() - self._cache_at <= self.CACHE_SECONDS
            )
            if cache_fresh:
                return self._apply_rectangle(
                    request_id,
                    handle,
                    reference,
                    expected_path,
                    runtime_document_id,
                    document_name,
                    self._cache_rectangle,
                )
            was_visible = (
                self._status.get("visible") is True
                and self._cache_key == target
            )
            if not was_visible:
                self.backend.hide()
            self._pending = (
                request_id,
                handle,
                reference,
                expected_path,
                runtime_document_id,
                document_name,
            )
            self._status = {
                "enabled": True,
                "visible": was_visible,
                "status": "refreshing" if was_visible else "scheduled",
                "selection_reference": reference,
            }
            self._ensure_worker()
            self._condition.notify()
            return dict(self._status)

    def set_enabled(self, enabled) -> dict:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                return self.hide("disabled")
            self._status["enabled"] = True
            if self._status.get("status") == "disabled":
                self._status["status"] = "hidden"
            return dict(self._status)

    def update(self, session, context) -> dict:
        target, reason = self._target(session, context)
        if reason:
            return self.hide(reason)
        (
            handle,
            reference,
            expected_path,
            runtime_document_id,
            document_name,
        ) = target
        with self._condition:
            if not self._enabled:
                return self.hide("disabled")
            self._request_id += 1
            request_id = self._request_id
            self._pending = None
        rectangle = self.locator.locate(
            handle,
            reference,
            expected_path,
            runtime_document_id,
            document_name,
        )
        return self._apply_rectangle(
            request_id,
            handle,
            reference,
            expected_path,
            runtime_document_id,
            document_name,
            rectangle,
        )

    def close(self):
        with self._condition:
            self._closed = True
            self._request_id += 1
            self._pending = None
            self._condition.notify_all()
        close_backend = getattr(self.backend, "close", None)
        if callable(close_backend):
            close_backend()
