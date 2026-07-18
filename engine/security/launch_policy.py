"""Central policy for targets launched as registered Windows applications."""

from __future__ import annotations

import os
import re
import struct


class UnsafeLaunchTarget(ValueError):
    """Raised when an application target is a shell, script, or console tool."""


BLOCKED_EXECUTABLE_STEMS = frozenset({
    "attrib", "bash", "bcdedit", "bitsadmin", "certutil", "cipher",
    "cmd", "compact", "cscript", "curl", "diskpart", "echo", "find",
    "findstr", "forfiles", "format", "fsutil", "ftp", "git", "icacls",
    "ipconfig", "java", "javac", "mklink", "mshta", "msiexec", "net",
    "netsh", "netstat", "node", "nodejs", "npm", "npx", "nslookup",
    "ping", "pip", "pip3", "powershell", "pwsh", "py", "python",
    "pythonw", "reg", "regsvr32", "robocopy", "rundll32", "sc", "scp",
    "schtasks", "sftp", "sh", "shutdown", "ssh", "systeminfo", "takeown",
    "taskkill", "tasklist", "telnet", "tracert", "vssadmin", "wbadmin",
    "wevtutil", "where", "whoami", "wmic", "wscript", "wsl", "xcopy",
})

BLOCKED_LAUNCH_EXTENSIONS = frozenset({
    ".bat", ".cmd", ".com", ".hta", ".js", ".jse", ".msi", ".ps1",
    ".py", ".pyw", ".vbs", ".vbe", ".wsf", ".wsh",
})

_SHELL_MARKERS = (
    "명령프롬프트", "명령 프롬프트", "cmd", "powershell", "power shell",
    "파워셸", "파워쉘", "터미널", "terminal", "shell", "셸", "쉘",
)
_SHELL_EXECUTION_MARKERS = (
    "실행", "돌려", "입력", "쳐줘", "명령 내려", "명령해", "/c", "-command",
)
_BLOCKED_PATH_FRAGMENTS = (
    "\\git\\usr\\bin\\",
    "\\git\\mingw32\\bin\\",
    "\\git\\mingw64\\bin\\",
    "\\windows\\system32\\windowspowershell\\",
)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def looks_like_shell_execution_request(text: str) -> bool:
    """Detect explicit requests to run text through a command shell."""
    normalized = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
    compact = normalized.replace(" ", "")
    has_shell = any(marker in normalized or marker.replace(" ", "") in compact for marker in _SHELL_MARKERS)
    has_execution = any(marker in normalized or marker.replace(" ", "") in compact for marker in _SHELL_EXECUTION_MARKERS)
    return bool(has_shell and has_execution)


def _shortcut_target(path: str) -> str:
    if os.path.splitext(path)[1].casefold() != ".lnk" or not os.path.isfile(path):
        return path
    try:
        import win32com.client

        shortcut = win32com.client.Dispatch("WScript.Shell").CreateShortCut(path)
        target = str(shortcut.Targetpath or "").strip().strip('"')
        return target or path
    except Exception as error:
        raise UnsafeLaunchTarget("바로가기의 실제 실행 대상을 안전하게 확인하지 못했습니다.") from error


def _pe_subsystem(path: str) -> int | None:
    """Return the PE subsystem value, or None for an unreadable/non-PE file."""
    if not os.path.isfile(path) or os.path.splitext(path)[1].casefold() != ".exe":
        return None
    try:
        with open(path, "rb") as stream:
            if stream.read(2) != b"MZ":
                return None
            stream.seek(0x3C)
            offset_raw = stream.read(4)
            if len(offset_raw) != 4:
                return None
            pe_offset = struct.unpack("<I", offset_raw)[0]
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                return None
            optional_header = pe_offset + 4 + 20
            stream.seek(optional_header)
            magic_raw = stream.read(2)
            if len(magic_raw) != 2 or struct.unpack("<H", magic_raw)[0] not in {0x10B, 0x20B}:
                return None
            stream.seek(optional_header + 68)
            subsystem_raw = stream.read(2)
            return struct.unpack("<H", subsystem_raw)[0] if len(subsystem_raw) == 2 else None
    except OSError:
        return None


def validate_launch_target(path: str, target_name: str = "") -> str:
    """Return a normalized safe target or raise ``UnsafeLaunchTarget``."""
    clean_path = os.path.expandvars(os.path.expanduser(str(path or "").strip().strip('"')))
    if not clean_path:
        raise UnsafeLaunchTarget("실행할 앱 경로가 비어 있습니다.")
    if _URL_RE.match(clean_path):
        return clean_path

    resolved = _shortcut_target(clean_path)
    normalized = os.path.normcase(resolved).replace("/", "\\")
    stem = os.path.splitext(os.path.basename(resolved))[0].casefold()
    extension = os.path.splitext(resolved)[1].casefold()
    display_name = str(target_name or stem or "대상").strip()

    if stem in BLOCKED_EXECUTABLE_STEMS:
        raise UnsafeLaunchTarget(
            f"보안상 '{display_name}' 콘솔·시스템 도구는 앱처럼 직접 실행할 수 없습니다."
        )
    if extension in BLOCKED_LAUNCH_EXTENSIONS:
        raise UnsafeLaunchTarget(
            f"보안상 '{extension}' 스크립트·명령 파일은 앱처럼 직접 실행할 수 없습니다."
        )
    if any(fragment in normalized for fragment in _BLOCKED_PATH_FRAGMENTS):
        raise UnsafeLaunchTarget(
            f"보안상 '{display_name}' 명령줄 도구 경로는 직접 실행할 수 없습니다."
        )
    # IMAGE_SUBSYSTEM_WINDOWS_CUI (3) identifies executables intended for a
    # console rather than a normal user-facing Windows application.
    if _pe_subsystem(resolved) == 3:
        raise UnsafeLaunchTarget(
            f"보안상 '{display_name}' 콘솔 프로그램은 앱처럼 직접 실행할 수 없습니다."
        )
    return clean_path


def is_safe_launch_target(path: str, target_name: str = "") -> bool:
    try:
        validate_launch_target(path, target_name)
        return True
    except UnsafeLaunchTarget:
        return False
