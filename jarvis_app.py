import os
import logging
import socket
import sys
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

        browser_mode = os.environ.get("JARVIS_BROWSER_MODE", "chrome")
        if browser_mode.lower() in {"none", "false", "off"}:
            browser_mode = None
        browser_port = _resolve_browser_port()
        logger.info("Starting Eel browser mode=%s port=%s", browser_mode, browser_port)
        eel.start(
            'index.html',
            mode=browser_mode,
            size=(1200, 800),
            port=browser_port,
            shutdown_delay=1.0,
        )
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
