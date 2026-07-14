import os
import sys
import traceback
from pathlib import Path
from engine.macro_worker import is_macro_worker, run_macro_worker

# Frozen children must dispatch here before Eel, APIs, and user data are loaded.
if is_macro_worker():
    sys.exit(run_macro_worker())

import eel
from engine.runtime_paths import USER_DATA_DIR, initialize_user_data, resource_path

def _write_startup_error(error):
    log_path = Path(USER_DATA_DIR).parent / "jarvis-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        encoding="utf-8",
    )

def start_app():
    try:
        # Initialize writable data before importing singleton-backed APIs.
        initialize_user_data()

        # Locate bundled resources correctly in both source and PyInstaller runs.
        eel.init(resource_path('gui'))

        # Import APIs here so packaged startup errors are captured in the log.
        import engine.api.command_api  # noqa: F401
        import engine.api.config_api  # noqa: F401
        import engine.api.dictionary_api  # noqa: F401

        browser_mode = os.environ.get("JARVIS_BROWSER_MODE", "chrome")
        if browser_mode.lower() in {"none", "false", "off"}:
            browser_mode = None
        try:
            browser_port = int(os.environ.get("JARVIS_PORT", "8080"))
        except ValueError:
            browser_port = 8080
        if not 1 <= browser_port <= 65535:
            browser_port = 8080
        eel.start(
            'index.html',
            mode=browser_mode,
            size=(1200, 800),
            port=browser_port,
            shutdown_delay=1.0,
        )
    except KeyboardInterrupt:
        print("Jarvis Closed.")
    except SystemExit:
        print("Jarvis Closed.")
    except Exception as e:
        print("Eel connection error:", e)
        _write_startup_error(e)
        sys.exit(1)

if __name__ == '__main__':
    start_app()
