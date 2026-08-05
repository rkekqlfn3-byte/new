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
    def caret_rectangle(window_handle: int):
        """Screen rectangle of the caret owned by ``window_handle``'s thread."""
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
        top_left = wintypes.POINT(info.rcCaret.left, info.rcCaret.top)
        bottom_right = wintypes.POINT(info.rcCaret.right, info.rcCaret.bottom)
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(top_left)):
            return None
        if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(bottom_right)):
            return None
        return (top_left.x, top_left.y, bottom_right.x, bottom_right.y)


class HwpCaretLocator:
    """Where to draw the caret marker, or nothing when it cannot be known."""

    def __init__(self, caret_api=None):
        self._caret_api = caret_api or Win32CaretApi()

    def locate(self, window_handle: int) -> ScreenRectangle | None:
        if not window_handle:
            return None
        try:
            bounds = self._caret_api.caret_rectangle(int(window_handle))
        except Exception:
            return None
        if not bounds:
            return None
        left, top, right, bottom = (int(value) for value in bounds)
        # A caret that has scrolled off the page reports a degenerate or
        # negative rectangle; marking it would point at nothing.
        if right < left or bottom < top:
            return None
        width = max(right - left, MIN_MARKER_WIDTH)
        height = max(bottom - top, MIN_MARKER_HEIGHT)
        rectangle = ScreenRectangle(left, top, left + width, top + height)
        return rectangle if rectangle.visible else None


__all__ = [
    "MIN_MARKER_HEIGHT",
    "MIN_MARKER_WIDTH",
    "HwpCaretLocator",
    "Win32CaretApi",
]
