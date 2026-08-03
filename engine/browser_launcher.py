"""Start the Jarvis GUI in an installed browser with explicit fallbacks."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserAttempt:
    browser: str
    success: bool
    reason: str


@dataclass(frozen=True)
class BrowserLaunchResult:
    selected_browser: str | None
    attempts: tuple[BrowserAttempt, ...]

    @property
    def success(self) -> bool:
        return self.selected_browser is not None


class BrowserLaunchError(RuntimeError):
    pass


def _existing_path(candidates):
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return None


def _registered_app_path(executable):
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None

    key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    views = [0]
    for name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
        value = getattr(winreg, name, 0)
        if value and value not in views:
            views.append(value)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(
                    hive, key_path, 0, winreg.KEY_READ | view
                ) as key:
                    value = winreg.QueryValue(key, None)
            except OSError:
                continue
            if value and Path(value).is_file():
                return str(Path(value))
    return None


def _application_path(executable, common_paths=()):
    return _existing_path((
        _registered_app_path(executable),
        shutil.which(executable),
        *common_paths,
    ))


def find_chrome():
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    return _application_path("chrome.exe", (
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
    ))


def find_edge():
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    return _application_path("msedge.exe", (
        os.path.join(program_files, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(program_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
    ))


def find_additional_browsers():
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = (
        (
            "Mozilla Firefox",
            _application_path("firefox.exe", (
                os.path.join(program_files, "Mozilla Firefox", "firefox.exe"),
                os.path.join(program_files_x86, "Mozilla Firefox", "firefox.exe"),
            )),
        ),
        (
            "Brave",
            _application_path("brave.exe", (
                os.path.join(program_files, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                os.path.join(program_files_x86, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                os.path.join(local, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            )),
        ),
    )
    return [(name, path) for name, path in candidates if path]


class BrowserLauncher:
    def __init__(
        self,
        chrome_finder=None,
        edge_finder=None,
        additional_finder=None,
        process_launcher=None,
        default_opener=None,
        preferred="chrome",
    ):
        self.chrome_finder = chrome_finder or find_chrome
        self.edge_finder = edge_finder or find_edge
        self.additional_finder = additional_finder or find_additional_browsers
        self.process_launcher = process_launcher or subprocess.Popen
        self.default_opener = default_opener or webbrowser.open
        self.preferred = str(preferred or "chrome").casefold()

    @staticmethod
    def _record_failure(attempts, browser, reason):
        attempt = BrowserAttempt(browser, False, str(reason))
        attempts.append(attempt)
        logger.warning(
            "GUI browser attempt failed browser=%s reason=%s",
            browser,
            reason,
        )

    def _launch_executable(self, browser, finder, url, attempts):
        try:
            executable = finder()
        except Exception as error:
            self._record_failure(
                attempts, browser, f"경로 검색 실패({type(error).__name__})"
            )
            return None
        if not executable:
            self._record_failure(attempts, browser, "설치 경로를 찾지 못했습니다.")
            return None
        try:
            self.process_launcher(
                [executable, f"--app={url}", "--disable-http-cache"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as error:
            self._record_failure(
                attempts, browser, f"실행 실패({type(error).__name__}: {error})"
            )
            return None
        attempts.append(BrowserAttempt(browser, True, f"실행 파일: {executable}"))
        logger.info(
            "GUI browser selected browser=%s path=%s prior_failures=%s",
            browser,
            executable,
            len(attempts) - 1,
        )
        return browser

    def launch(self, url):
        attempts = []
        primary = [
            ("Google Chrome", self.chrome_finder),
            ("Microsoft Edge", self.edge_finder),
        ]
        if self.preferred == "edge":
            primary.reverse()

        for browser, finder in primary:
            selected = self._launch_executable(
                browser, finder, url, attempts
            )
            if selected:
                return BrowserLaunchResult(selected, tuple(attempts))

        try:
            opened = bool(self.default_opener(url, new=1, autoraise=True))
        except Exception as error:
            self._record_failure(
                attempts,
                "시스템 기본 브라우저",
                f"실행 실패({type(error).__name__}: {error})",
            )
        else:
            if opened:
                selected = "시스템 기본 브라우저"
                attempts.append(BrowserAttempt(selected, True, "운영체제 기본 앱"))
                logger.info(
                    "GUI browser selected browser=%s prior_failures=%s",
                    selected,
                    len(attempts) - 1,
                )
                return BrowserLaunchResult(selected, tuple(attempts))
            self._record_failure(
                attempts, "시스템 기본 브라우저", "기본 브라우저가 요청을 거부했습니다."
            )

        try:
            additional = list(self.additional_finder())
        except Exception as error:
            self._record_failure(
                attempts,
                "추가 브라우저 검색",
                f"검색 실패({type(error).__name__})",
            )
            additional = []
        if not additional:
            self._record_failure(
                attempts, "추가 브라우저", "사용 가능한 실행 파일을 찾지 못했습니다."
            )
        for browser, executable in additional:
            selected = self._launch_executable(
                browser, lambda path=executable: path, url, attempts
            )
            if selected:
                return BrowserLaunchResult(selected, tuple(attempts))

        logger.error("All GUI browser launch attempts failed attempts=%s", len(attempts))
        return BrowserLaunchResult(None, tuple(attempts))


def format_browser_failure(result, log_path=None):
    details = "\n".join(
        f"- {attempt.browser}: {attempt.reason}"
        for attempt in result.attempts
        if not attempt.success
    )
    log_hint = f"\n로그 위치: {log_path}" if log_path else ""
    return (
        "Jarvis 화면을 열 브라우저를 실행하지 못했습니다.\n\n"
        f"시도 결과:\n{details}\n\n"
        "해결 방법:\n"
        "1) Chrome 또는 Microsoft Edge가 설치되어 있는지 확인하세요.\n"
        "2) Windows 설정 > 앱 > 기본 앱에서 웹 브라우저를 지정하세요.\n"
        "3) 보안 프로그램이 Jarvis의 브라우저 실행을 차단했는지 확인한 뒤 다시 실행하세요."
        f"{log_hint}"
    )


def show_browser_failure(message, title="Jarvis 브라우저 실행 오류"):
    logger.error("GUI browser startup aborted: %s", message.replace("\n", " | "))
    print(message, file=sys.stderr)
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        logger.exception("Could not display browser failure dialog")
