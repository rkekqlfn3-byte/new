"""Verify frozen edit-mode busy recovery without touching a live document."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    output = Path(sys.argv[1]).resolve()

    from engine.edit_mode.context import EditContextBusy, NativeDocumentContextReader
    from engine.edit_mode.intake import EditAppBusy, FileIntakeManager
    from engine.edit_mode.native_bridge import NativeOfficeBusy
    from engine.edit_mode.selection_overlay import (
        ScreenRectangle,
        SelectionOverlayManager,
        normalized_excel_range,
    )
    from engine.edit_mode.window_layout import DocumentWindowActivator
    from engine.version import runtime_info

    fixture = output.with_name("busy-probe.xlsx")
    fixture.write_bytes(b"frozen busy retry fixture")
    metadata = {
        "app_type": "excel",
        "file_path": str(fixture),
        "document_name": fixture.name,
        "window_handle": 1,
        "active_container": "Sheet1",
        "selection_reference": "G16",
    }

    class RecoveringBridge:
        def __init__(self):
            self.launches = []

        @staticmethod
        def is_available(app_type):
            return app_type == "excel"

        @staticmethod
        def find_document(app_type, expected_path=None):
            raise NativeOfficeBusy("Excel busy")

        @staticmethod
        def wait_for_document(app_type, expected_path, timeout=15.0):
            return dict(metadata)

        def launch_document(self, app_type, file_path):
            self.launches.append((app_type, file_path))

    bridge = RecoveringBridge()
    connected = FileIntakeManager(bridge, open_timeout=1, busy_timeout=.5).connect_file(
        str(fixture)
    )

    class FlakyReader(NativeDocumentContextReader):
        def __init__(self, failures):
            runtime = SimpleNamespace(
                CoInitialize=lambda: None,
                CoUninitialize=lambda: None,
            )
            super().__init__(
                runtime,
                busy_retry_attempts=3,
                busy_retry_delay=0,
            )
            self.failures = failures
            self.attempts = 0

        def _capture_office_document(self, app_type, expected_path):
            self.attempts += 1
            if self.attempts <= self.failures:
                raise NativeOfficeBusy("Excel busy")
            return {
                "app_type": app_type,
                "file_path": expected_path,
                "selection_reference": "G16",
            }

    recovered_reader = FlakyReader(failures=2)
    recovered_context = recovered_reader.capture("excel", str(fixture))
    blocked_reader = FlakyReader(failures=10)
    blocked_error = None
    try:
        blocked_reader.capture("excel", str(fixture))
    except EditContextBusy as error:
        blocked_error = error

    class FrozenOverlayLocator:
        @staticmethod
        def locate(handle, reference):
            if handle == 1 and reference == "G16":
                return ScreenRectangle(10, 20, 90, 45)
            return None

    class FrozenOverlayBackend:
        def __init__(self):
            self.shown = []
            self.hidden = 0

        def show(self, owner_handle, rectangle, label):
            self.shown.append((owner_handle, rectangle, label))
            return True

        def hide(self):
            self.hidden += 1

    overlay_backend = FrozenOverlayBackend()
    overlay_manager = SelectionOverlayManager(
        locator=FrozenOverlayLocator(),
        backend=overlay_backend,
    )
    overlay_status = overlay_manager.update(
        {"app_type": "excel", "window_handle": 1},
        {"selection_kind": "range", "selection_reference": "$G$16"},
    )
    overlay_disabled = overlay_manager.set_enabled(False)

    class FrozenActivationBackend:
        def __init__(self):
            self.foreground = 99
            self.requests = []

        @staticmethod
        def is_window(handle):
            return handle == 1

        def foreground_root(self):
            return self.foreground

        @staticmethod
        def restore(handle):
            return None

        def request_foreground(self, handle):
            self.requests.append(handle)
            self.foreground = handle

    activation_backend = FrozenActivationBackend()
    activation_status = DocumentWindowActivator(
        backend=activation_backend,
        attempts=1,
        retry_delay=0,
    ).activate(1)

    results = {
        "frozen_runtime": runtime_info().get("frozen") is True,
        "connection_recovered_without_launch": (
            connected.get("selection_reference") == "G16"
            and connected.get("launch_requested") is False
            and bridge.launches == []
        ),
        "selection_recovered_after_retry": (
            recovered_context.get("selection_reference") == "G16"
            and recovered_reader.attempts == 3
        ),
        "selection_overlay_contract": (
            normalized_excel_range("$G$16") == ("G16", "G16")
            and overlay_status.get("visible") is True
            and overlay_status.get("selection_reference") == "G16"
            and len(overlay_backend.shown) == 1
            and overlay_disabled.get("status") == "disabled"
            and overlay_backend.hidden >= 1
        ),
        "document_activation_contract": (
            activation_status.get("status") == "focused"
            and activation_status.get("focused") is True
            and activation_backend.requests == [1]
        ),
        "runtime_info": runtime_info(),
    }
    # EditContextBusy and EditAppBusy intentionally share the public retryable
    # contract even though they belong to different API boundaries.
    results["persistent_busy_is_retryable"] = (
        blocked_error is not None
        and blocked_error.status == "busy"
        and blocked_error.retryable is True
        and blocked_reader.attempts == 3
        and EditAppBusy.status == "busy"
        and EditAppBusy.retryable is True
    )
    results["all_passed"] = all(
        value for key, value in results.items()
        if key not in {"runtime_info", "all_passed"}
    )
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fixture.unlink(missing_ok=True)
    if not results["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
