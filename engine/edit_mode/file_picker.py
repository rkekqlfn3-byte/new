"""Windows native file picker for local edit documents."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class FilePickerError(RuntimeError):
    error_type = "environment_error"
    status = "failed"


class _OpenFileNameW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


DOCUMENT_FILTER = (
    "지원 문서\0*.xlsx;*.xlsm;*.xlsb;*.xls;*.hwp;*.hwpx;"
    "*.docx;*.docm;*.doc;*.pptx;*.pptm;*.ppt\0"
    "Excel\0*.xlsx;*.xlsm;*.xlsb;*.xls\0"
    "한글\0*.hwp;*.hwpx\0"
    "Word\0*.docx;*.docm;*.doc\0"
    "PowerPoint\0*.pptx;*.pptm;*.ppt\0\0"
)


def choose_edit_document() -> str | None:
    """Return a selected absolute path, or None when the user cancels."""
    if os.name != "nt":
        raise FilePickerError("문서 선택 창은 Windows에서만 지원합니다.")
    file_buffer = ctypes.create_unicode_buffer(32768)
    request = _OpenFileNameW()
    request.lStructSize = ctypes.sizeof(_OpenFileNameW)
    request.lpstrFilter = DOCUMENT_FILTER
    request.nFilterIndex = 1
    request.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    request.nMaxFile = len(file_buffer)
    request.lpstrTitle = "JARVIS에서 편집할 문서 선택"
    request.Flags = 0x00001000 | 0x00000800 | 0x00000008

    result = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(request))
    if result:
        return os.path.abspath(file_buffer.value)
    error_code = int(ctypes.windll.comdlg32.CommDlgExtendedError())
    if error_code == 0:
        return None
    raise FilePickerError(f"Windows 문서 선택 창 오류가 발생했습니다: {error_code}")
