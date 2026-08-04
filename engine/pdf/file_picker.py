"""Windows native file picker limited to local PDF documents."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class PdfFilePickerError(RuntimeError):
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


PDF_FILTER = "PDF 문서\0*.pdf\0\0"
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000


def choose_pdf_document() -> str | None:
    """Return one selected absolute PDF path, or None on user cancellation."""
    if os.name != "nt":
        raise PdfFilePickerError("PDF 선택 창은 Windows에서만 지원합니다.")
    file_buffer = ctypes.create_unicode_buffer(32768)
    request = _OpenFileNameW()
    request.lStructSize = ctypes.sizeof(_OpenFileNameW)
    request.lpstrFilter = PDF_FILTER
    request.nFilterIndex = 1
    request.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    request.nMaxFile = len(file_buffer)
    request.lpstrTitle = "JARVIS에서 읽을 PDF 선택"
    request.lpstrDefExt = "pdf"
    request.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR
    result = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(request))
    if result:
        return os.path.abspath(file_buffer.value)
    error_code = int(ctypes.windll.comdlg32.CommDlgExtendedError())
    if error_code == 0:
        return None
    raise PdfFilePickerError(f"Windows PDF 선택 창 오류가 발생했습니다: {error_code}")
