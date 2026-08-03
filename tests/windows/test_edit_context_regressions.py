"""Windows Office-context regressions using owned metadata and test doubles only."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from engine.edit_mode.context import EditContextInactive
from engine.edit_mode.controller import EditModeController
from engine.edit_mode.intake import FileIntakeManager
from engine.edit_mode.native_bridge import (
    NativeDocumentBridge,
    _deduplicate_active_documents,
)
from engine.edit_mode.selection_overlay import (
    ScreenRectangle,
    SelectionOverlayManager,
)
from engine.edit_mode.session import EditSessionManager


class ExcelInstanceSelectionTests(unittest.TestCase):
    def test_same_saved_path_keeps_the_frontmost_excel_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "shared.xlsx"
            workbook.write_bytes(b"fixture")
            front = {
                "app_type": "excel",
                "file_path": str(workbook),
                "document_name": workbook.name,
                "window_handle": 101,
                "is_saved": True,
            }
            behind = {**front, "window_handle": 202}
            bridge = NativeDocumentBridge()

            with (
                mock.patch.object(
                    bridge,
                    "_excel_window_documents",
                    return_value=[behind, front],
                ),
                mock.patch(
                    "win32com.client.GetActiveObject",
                    side_effect=RuntimeError("not registered"),
                ),
                mock.patch.object(
                    bridge, "_rot_active_office_documents", return_value=[]
                ),
                mock.patch.object(
                    bridge, "_window_z_order", return_value={101: 0, 202: 4}
                ),
            ):
                documents = bridge._active_office_documents("excel")

        self.assertEqual(1, len(documents))
        self.assertEqual(101, documents[0]["window_handle"])
        self.assertEqual(0, documents[0]["window_rank"])

    def test_unsaved_workbooks_with_same_name_remain_distinct_by_window(self):
        documents = _deduplicate_active_documents([
            {
                "app_type": "excel",
                "file_path": "",
                "document_name": "Book1",
                "runtime_document_id": "RUNTIME-A",
                "window_handle": 101,
                "window_rank": 2,
            },
            {
                "app_type": "excel",
                "file_path": "",
                "document_name": "Book1",
                "runtime_document_id": "RUNTIME-B",
                "window_handle": 202,
                "window_rank": 0,
            },
        ])

        self.assertEqual([202, 101], [item["window_handle"] for item in documents])

    def test_frontmost_unsaved_workbook_receives_its_exact_runtime_token(self):
        class Bridge:
            @staticmethod
            def active_documents(app_type=None):
                return [
                    {
                        "app_type": "excel",
                        "file_path": "",
                        "document_name": "Book1",
                        "window_handle": 101,
                        "window_rank": 3,
                        "is_saved": False,
                    },
                    {
                        "app_type": "excel",
                        "file_path": "",
                        "document_name": "Book1",
                        "window_handle": 202,
                        "window_rank": 0,
                        "is_saved": False,
                    },
                ]

            @staticmethod
            def bind_runtime_excel_document(document):
                return f"RUNTIME-{document['window_handle']}"

        document = FileIntakeManager(bridge=Bridge()).connect_active_document(
            "excel"
        )

        self.assertEqual(202, document["window_handle"])
        self.assertEqual("RUNTIME-202", document["runtime_document_id"])
        self.assertEqual("runtime", document["identity_kind"])


class _Layout:
    enabled = True

    @staticmethod
    def arrange(session_id, document_handle):
        return {"success": True, "status": "arranged", "arranged": True}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class _Activator:
    def __init__(self):
        self.focused_handle = 0
        self.handles = []

    def activate(self, handle):
        self.focused_handle = int(handle)
        self.handles.append(int(handle))
        return {"success": True, "status": "focused", "focused": True}


class _Overlay:
    def __init__(self):
        self.scheduled = []
        self.hidden = []

    def schedule(self, session, context):
        self.scheduled.append((dict(session), dict(context)))
        return {
            "enabled": True,
            "visible": True,
            "status": "shown",
            "selection_reference": context.get("selection_reference"),
        }

    def hide(self, reason="hidden"):
        self.hidden.append(reason)
        return {
            "enabled": True,
            "visible": False,
            "status": reason,
            "selection_reference": None,
        }

    @staticmethod
    def status():
        return {"enabled": True, "visible": False, "status": "hidden"}


class _MutableIntake:
    def __init__(self, document):
        self.document = dict(document)

    def connect_active_document(self, app_type=None):
        return dict(self.document)


class _FocusAwareContext:
    def __init__(self, activator):
        self.activator = activator
        self.calls = 0

    def capture(self, session):
        self.calls += 1
        current = dict(session)
        if self.activator.focused_handle != current["window_handle"]:
            raise EditContextInactive("connected document is not foreground")
        return {
            "session_id": current["session_id"],
            "app_type": current["app_type"],
            "file_path": current["file_path"],
            "document_name": current["document_name"],
            "document_fingerprint": current["document_fingerprint"],
            "context_fingerprint": "C" * 64,
            "active_container": "Sheet1",
            "selection_reference": "B2:D4",
            "selection_kind": "range",
            "target": {"sheet_name": "Sheet1", "address": "B2:D4"},
        }


class EditConnectionFocusTests(unittest.TestCase):
    def _controller(self, workbook, handle=101):
        intake = _MutableIntake({
            "app_type": "excel",
            "file_path": str(workbook),
            "document_name": workbook.name,
            "window_handle": handle,
            "active_container": "Sheet1",
            "selection_reference": "B2:D4",
            "is_saved": True,
        })
        activator = _Activator()
        context = _FocusAwareContext(activator)
        overlay = _Overlay()
        controller = EditModeController(
            intake_manager=intake,
            session_manager=EditSessionManager(),
            layout_manager=_Layout(),
            context_manager=context,
            selection_overlay_manager=overlay,
            window_activator=activator,
        )
        return controller, intake, activator, context, overlay

    def test_background_document_is_focused_then_context_and_selection_recovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook = Path(temp_dir) / "background.xlsx"
            workbook.write_bytes(b"fixture")
            controller, _, activator, context, overlay = self._controller(workbook)

            connected = controller.connect_active_document("excel")

        self.assertEqual([101], activator.handles)
        self.assertEqual(2, context.calls)
        self.assertTrue(connected["context_recovered_after_focus"])
        self.assertIsNone(connected["context_error"])
        self.assertEqual("B2:D4", connected["context"]["selection_reference"])
        self.assertEqual("B2:D4", overlay.scheduled[-1][1]["selection_reference"])

    def test_reconnect_replaces_session_and_focuses_the_new_document(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.xlsx"
            second = Path(temp_dir) / "second.xlsx"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            controller, intake, activator, _, _ = self._controller(first)
            first_session = controller.connect_active_document("excel")
            intake.document.update({
                "file_path": str(second),
                "document_name": second.name,
                "window_handle": 202,
            })

            second_session = controller.connect_active_document("excel")

        self.assertNotEqual(first_session["session_id"], second_session["session_id"])
        self.assertEqual(202, controller.session_manager.current()["window_handle"])
        self.assertEqual([101, 202], activator.handles)


class _BlockingLocator:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def locate(
        self,
        handle,
        reference,
        expected_path=None,
        runtime_document_id=None,
        document_name=None,
    ):
        if int(handle) == 101:
            self.started.set()
            self.release.wait(2)
        return ScreenRectangle(handle, 10, handle + 20, 30)


class _OverlayBackend:
    def __init__(self):
        self.shown = []

    def show(self, handle, rectangle, label):
        self.shown.append((handle, rectangle, label))
        return True

    def hide(self):
        return None

    def close(self):
        return None


class SelectionInstanceIsolationTests(unittest.TestCase):
    def test_late_lookup_from_old_excel_instance_never_draws_over_new_instance(self):
        locator = _BlockingLocator()
        backend = _OverlayBackend()
        manager = SelectionOverlayManager(locator=locator, backend=backend)
        context = {"selection_kind": "range", "selection_reference": "A1"}
        first = {
            "app_type": "excel",
            "file_path": r"C:\work\same.xlsx",
            "document_name": "same.xlsx",
            "window_handle": 101,
        }
        second = {**first, "window_handle": 202}

        manager.schedule(first, context)
        self.assertTrue(locator.started.wait(1))
        manager.schedule(second, context)
        locator.release.set()
        deadline = time.monotonic() + 2
        while not backend.shown and time.monotonic() < deadline:
            time.sleep(0.01)
        manager.close()

        self.assertEqual([202], [item[0] for item in backend.shown])


if __name__ == "__main__":
    unittest.main()
