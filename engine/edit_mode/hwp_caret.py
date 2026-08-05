"""Locate the 한글 caret on screen so it can be marked.

한글's own caret is a one-pixel line, and a preview that says "3쪽 12줄 30칸"
still leaves the reader hunting for it.  This finds where the caret actually
is in screen coordinates so the existing overlay can draw a marker there.

The position does not come from 한글 at all.  ``GetGUIThreadInfo`` reports the
caret rectangle for a window's thread, which is a Windows fact rather than an
application API, so it needs no COM call, no document read, and cannot disturb
the document or the caret it is describing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from engine.edit_mode.selection_overlay import ScreenRectangle

# 한글 reports a caret one pixel wide and one pixel tall, which no one can see.
# A marker is drawn at least this big so it reads as a place on the page.
MIN_MARKER_WIDTH = 3
MIN_MARKER_HEIGHT = 20

# A minimised window reports its client area near -32000, so a caret read from
# one is arithmetically valid and points nowhere a person can look.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", _Rect),
    ]


class Win32CaretApi:
    """The Windows calls this needs, isolated so tests can replace them."""

    @staticmethod
    def virtual_screen():
        """Bounds of all monitors together, used to reject off-screen reads."""
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if width <= 0 or height <= 0:
            return None
        return (left, top, left + width, top + height)

    @staticmethod
    def caret_rectangle(window_handle: int):
        """Screen rectangle of the caret owned by ``window_handle``'s thread.

        Returns ``None`` unless 한글 is the foreground window.  ``hwndFocus``
        is per-thread and survives losing focus, so it cannot answer this; the
        foreground window can.  A read taken while another window is in front
        is not trustworthy, and the caller has a remembered position that is.
        """
        user32 = ctypes.windll.user32
        thread_id = user32.GetWindowThreadProcessId(
            wintypes.HWND(int(window_handle)), None
        )
        if not thread_id:
            return None
        info = _GuiThreadInfo()
        info.cbSize = ctypes.sizeof(_GuiThreadInfo)
        if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None
        if not info.hwndCaret:
            return None
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return None
        if user32.GetWindowThreadProcessId(
            wintypes.HWND(foreground), None
        ) != thread_id:
            return None
        top_left = wintypes.POINT(info.rcCaret.left, info.rcCaret.top)
        bottom_right = wintypes.POINT(info.rcCaret.right, info.rcCaret.bottom)
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(top_left)):
            return None
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(bottom_right)):
            return None
        return (top_left.x, top_left.y, bottom_right.x, bottom_right.y)


class HwpCaretLocator:
    """Where to draw the caret marker, or nothing when it cannot be known.

    The last position seen while 한글 held focus is remembered, because the
    marker is most needed exactly when it cannot be read: the moment the user
    clicks into the command box to decide what to ask for, 한글 loses focus and
    its caret is gone. The caret has not moved — only the focus has — so the
    remembered place is still the right answer.
    """

    def __init__(self, caret_api=None):
        self._caret_api = caret_api or Win32CaretApi()
        self._remembered: dict[int, ScreenRectangle] = {}

    def _on_screen(self, rectangle: ScreenRectangle) -> bool:
        screen = getattr(self._caret_api, "virtual_screen", None)
        bounds = screen() if callable(screen) else None
        if not bounds:
            return True
        left, top, right, bottom = bounds
        return (
            left <= rectangle.left < right and top <= rectangle.top < bottom
        )

    def forget(self, window_handle: int | None = None) -> None:
        if window_handle is None:
            self._remembered.clear()
        else:
            self._remembered.pop(int(window_handle), None)

    def locate(self, window_handle: int) -> ScreenRectangle | None:
        if not window_handle:
            return None
        handle = int(window_handle)
        try:
            bounds = self._caret_api.caret_rectangle(handle)
        except Exception:
            bounds = None
        if not bounds:
            return self._remembered.get(handle)
        left, top, right, bottom = (int(value) for value in bounds)
        # A caret that has scrolled off the page reports a degenerate or
        # negative rectangle; marking it would point at nothing.
        if right < left or bottom < top:
            return self._remembered.get(handle)
        width = max(right - left, MIN_MARKER_WIDTH)
        height = bottom - top
        if height >= MIN_MARKER_HEIGHT:
            marker_top = top
        else:
            # 한글 registers its one-pixel caret at the *bottom* of the line,
            # not the top: measured against a real 한글, the glyphs of the
            # caret's own line ended one pixel above the reported point. A
            # marker grown downwards from there lands on the line below and
            # reads as pointing at the wrong place, so it grows upwards.
            height = MIN_MARKER_HEIGHT
            marker_top = bottom - height
        rectangle = ScreenRectangle(
            left, marker_top, left + width, marker_top + height
        )
        if not rectangle.visible or not self._on_screen(rectangle):
            return self._remembered.get(handle)
        self._remembered[handle] = rectangle
        return rectangle


__all__ = [
    "MIN_MARKER_HEIGHT",
    "MIN_MARKER_WIDTH",
    "HwpCaretLocator",
    "Win32CaretApi",
]
