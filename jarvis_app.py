import logging
import os
import socket
import sys
import time
import traceback
from pathlib import Path

from engine.macro_worker import is_macro_worker, run_macro_worker

logger = logging.getLogger(__name__)


def _resolve_browser_port():
    """Use JARVIS_PORT when set, otherwise pick a free ephemeral port.

    Defaulting to a fixed 8080 makes startup fail when another program already
    holds it. An explicit free port avoids that while keeping JARVIS_PORT as a
    debugging override.
    """
    override = os.environ.get("JARVIS_PORT")
    if override:
        try:
            port = int(override)
        except ValueError:
            port = 0
        if 1 <= port <= 65535:
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]

# Frozen children must dispatch here before Eel, APIs, and user data are loaded.
if is_macro_worker():
    sys.exit(run_macro_worker())

import eel

from engine.browser_launcher import (
    BrowserLauncher,
    BrowserLaunchError,
    format_browser_failure,
    show_browser_failure,
)
from engine.logging_config import configure_logging, redact_text
from engine.runtime_paths import USER_DATA_DIR, initialize_user_data, resource_path
from engine.version import runtime_info


def _write_startup_error(error):
    if any(
        getattr(handler, "_jarvis_rotating_handler", False)
        for handler in logging.getLogger().handlers
    ):
        return
    log_path = Path(USER_DATA_DIR).parent / "jarvis-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        redact_text("".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )),
        encoding="utf-8",
    )


def _browser_preference():
    value = os.environ.get("JARVIS_BROWSER_MODE", "chrome").strip().casefold()
    if value in {"none", "false", "off"}:
        return None
    return "edge" if value == "edge" else "chrome"


def _wait_for_gui_server(port, timeout=5.0):
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            eel.sleep(0.05)
    return False


def _start_gui(browser_port, log_path, launcher=None):
    preference = _browser_preference()
    logger.info(
        "Starting Eel browser strategy=%s preference=%s port=%s",
        "disabled" if preference is None else "fallback",
        preference,
        browser_port,
    )
    eel.start(
        'index.html',
        mode=None,
        block=False,
        size=(1200, 800),
        port=browser_port,
        shutdown_delay=1.0,
    )
    if preference is not None:
        if not _wait_for_gui_server(browser_port):
            message = (
                "Jarvis의 로컬 GUI 서버가 시작되지 않았습니다.\n\n"
                "다른 프로그램이 포트를 점유했는지 확인하고 Jarvis를 다시 실행하세요.\n"
                f"로그 위치: {log_path}"
            )
            show_browser_failure(message, title="Jarvis GUI 시작 오류")
            raise BrowserLaunchError(message)
        active_launcher = launcher or BrowserLauncher(preferred=preference)
        result = active_launcher.launch(
            f"http://127.0.0.1:{browser_port}/index.html"
        )
        if not result.success:
            message = format_browser_failure(result, log_path)
            show_browser_failure(message)
            raise BrowserLaunchError(message)

    while True:
        eel.sleep(1.0)

def start_app():
    try:
        # Initialize writable data before importing singleton-backed APIs.
        initialize_user_data()
        log_path = configure_logging()
        logger.info("Jarvis starting: %s (log=%s)", runtime_info(), log_path)

        # Construct the process-wide parser only after writable paths exist.
        from engine.core import initialize_core

        initialize_core()

        # Locate bundled resources correctly in both source and PyInstaller runs.
        eel.init(resource_path('gui'))

        # Import APIs here so packaged startup errors are captured in the log.
        import engine.api.command_api  # noqa: F401
        import engine.api.config_api  # noqa: F401
        import engine.api.dictionary_api  # noqa: F401
        import engine.api.edit_api  # noqa: F401

        browser_port = _resolve_browser_port()
        _start_gui(browser_port, log_path)
    except KeyboardInterrupt:
        logger.info("Jarvis closed by keyboard interrupt")
        print("Jarvis Closed.")
    except SystemExit:
        logger.info("Jarvis closed")
        print("Jarvis Closed.")
    except Exception as e:
        logger.exception("Jarvis startup failed")
        print("Jarvis startup failed. See the application log for details.")
        _write_startup_error(e)
        sys.exit(1)

if __name__ == '__main__':
    start_app()
