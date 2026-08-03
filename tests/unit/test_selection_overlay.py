import threading
import time
import unittest
from types import SimpleNamespace

from engine.edit_mode.selection_overlay import (
    ExcelComSelectionLocator,
    ExcelSelectionLocator,
    ScreenRectangle,
    SelectionOverlayManager,
    normalized_excel_range,
)


class FakeCell:
    def __init__(self, rectangle, visible=True):
        self._rectangle = rectangle
        self._visible = visible

    def wrapper_object(self):
        return self

    def is_visible(self):
        return self._visible

    def rectangle(self):
        return SimpleNamespace(
            left=self._rectangle.left,
            top=self._rectangle.top,
            right=self._rectangle.right,
            bottom=self._rectangle.bottom,
        )


class FakeWindow:
    def __init__(self, cells):
        self.cells = cells

    def child_window(self, *, auto_id, control_type):
        if control_type != "DataItem" or auto_id not in self.cells:
            raise LookupError(auto_id)
        return self.cells[auto_id]


class FakeDesktop:
    def __init__(self, window):
        self._window = window

    def window(self, *, handle):
        return self._window


class FakeWindowApi:
    def __init__(self, usable=True):
        self._usable = usable

    def usable(self, handle):
        return self._usable and handle == 10

    @staticmethod
    def rectangle(handle):
        return ScreenRectangle(0, 0, 1000, 800)


class FakeBackend:
    def __init__(self):
        self.shown = []
        self.hidden = 0

    def show(self, owner_handle, rectangle, label):
        self.shown.append((owner_handle, rectangle, label))
        return True

    def hide(self):
        self.hidden += 1


class SelectionOverlayTests(unittest.TestCase):
    def test_excel_range_validation_is_bounded(self):
        self.assertEqual(("G16", "G16"), normalized_excel_range("$g$16"))
        self.assertEqual(("B3", "F18"), normalized_excel_range("B3:F18"))
        self.assertIsNone(normalized_excel_range("A0"))
        self.assertIsNone(normalized_excel_range("XFE1"))
        self.assertIsNone(normalized_excel_range("A1,B2"))

    def test_locator_uses_uia_corner_rectangles_without_focus_changes(self):
        window = FakeWindow({
            "B3": FakeCell(ScreenRectangle(100, 200, 160, 225)),
            "F18": FakeCell(ScreenRectangle(360, 500, 430, 525)),
        })
        locator = ExcelSelectionLocator(
            desktop_factory=lambda: FakeDesktop(window),
            window_api=FakeWindowApi(),
        )
        self.assertEqual(
            ScreenRectangle(100, 200, 430, 525),
            locator.locate(10, "B3:F18"),
        )
        self.assertIsNone(locator.locate(11, "B3:F18"))

    def test_locator_hides_when_any_corner_is_offscreen(self):
        window = FakeWindow({
            "B3": FakeCell(ScreenRectangle(100, 200, 160, 225)),
            "F18": FakeCell(ScreenRectangle(0, 0, 0, 0), visible=False),
        })
        locator = ExcelSelectionLocator(
            desktop_factory=lambda: FakeDesktop(window),
            window_api=FakeWindowApi(),
        )
        self.assertIsNone(locator.locate(10, "B3:F18"))

    def test_com_fast_path_matches_excel_screen_geometry(self):
        selected = SimpleNamespace(
            Left=84.0,
            Top=285.0,
            Width=270.0,
            Height=114.0,
        )
        visible = SimpleNamespace(Left=0.0, Top=0.0)

        class Window:
            FreezePanes = False
            SplitRow = 0
            SplitColumn = 0
            Zoom = 100
            VisibleRange = visible

            @staticmethod
            def PointsToScreenPixelsX(value):
                return 30

            @staticmethod
            def PointsToScreenPixelsY(value):
                return 286

        class Sheet:
            @staticmethod
            def Range(reference):
                self.assertEqual("B14:D21", reference)
                return selected

        application = SimpleNamespace(
            Hwnd=10,
            ActiveWindow=Window(),
            ActiveSheet=Sheet(),
            Intersect=lambda first, second: first,
        )
        runtime = SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
        window_api = SimpleNamespace(
            usable=lambda handle: handle == 10,
            rectangle=lambda handle: ScreenRectangle(0, 0, 1200, 1000),
        )
        locator = ExcelComSelectionLocator(
            application_factory=lambda: application,
            window_api=window_api,
            com_runtime=runtime,
            dpi_getter=lambda handle: 96,
        )
        handled, rectangle = locator.try_locate(10, "B14:D21")
        self.assertTrue(handled)
        self.assertEqual(ScreenRectangle(142, 666, 502, 818), rectangle)

    def test_async_schedule_coalesces_and_reuses_recent_rectangle(self):
        started = threading.Event()
        release = threading.Event()

        class SlowLocator:
            def __init__(self):
                self.calls = []

            def locate(
                self,
                handle,
                reference,
                expected_path=None,
                runtime_document_id=None,
                document_name=None,
            ):
                self.calls.append((reference, expected_path))
                started.set()
                release.wait(2)
                return ScreenRectangle(100, 200, 180, 230)

        locator = SlowLocator()
        backend = FakeBackend()
        manager = SelectionOverlayManager(locator=locator, backend=backend)
        session = {
            "app_type": "excel",
            "window_handle": 10,
            "file_path": r"C:\work\first.xlsx",
        }
        first_context = {"selection_kind": "range", "selection_reference": "G16"}
        latest_context = {"selection_kind": "range", "selection_reference": "H17"}

        start = time.perf_counter()
        scheduled = manager.schedule(session, first_context)
        self.assertLess(time.perf_counter() - start, 0.1)
        self.assertEqual("scheduled", scheduled["status"])
        self.assertTrue(started.wait(1))
        manager.schedule(session, latest_context)
        release.set()

        deadline = time.monotonic() + 2
        while not backend.shown and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(
            [
                ("G16", r"C:\work\first.xlsx"),
                ("H17", r"C:\work\first.xlsx"),
            ],
            locator.calls,
        )
        self.assertEqual("JARVIS · H17", backend.shown[-1][2])

        calls_before_cache = list(locator.calls)
        cached = manager.schedule(session, latest_context)
        self.assertEqual("shown", cached["status"])
        self.assertEqual(calls_before_cache, locator.calls)
        manager.close()

    def test_manager_draws_only_supported_excel_ranges_and_can_be_disabled(self):
        class Locator:
            @staticmethod
            def locate(
                handle,
                reference,
                expected_path=None,
                runtime_document_id=None,
                document_name=None,
            ):
                return ScreenRectangle(100, 200, 180, 230)

        backend = FakeBackend()
        manager = SelectionOverlayManager(locator=Locator(), backend=backend)
        session = {
            "app_type": "excel",
            "window_handle": 10,
            "file_path": r"C:\work\first.xlsx",
        }
        context = {"selection_kind": "range", "selection_reference": "$G$16"}
        status = manager.update(session, context)
        self.assertTrue(status["visible"])
        self.assertEqual("G16", status["selection_reference"])
        self.assertEqual("JARVIS · G16", backend.shown[-1][2])

        disabled = manager.set_enabled(False)
        self.assertFalse(disabled["visible"])
        self.assertEqual("disabled", disabled["status"])
        self.assertGreaterEqual(backend.hidden, 1)

        reenabled = manager.set_enabled(True)
        self.assertEqual("hidden", reenabled["status"])
        unsupported = manager.update(
            {"app_type": "word", "window_handle": 10},
            context,
        )
        self.assertEqual("unsupported_app", unsupported["status"])

    def test_unsaved_excel_marker_is_bound_to_runtime_document(self):
        calls = []

        class Locator:
            @staticmethod
            def locate(
                handle,
                reference,
                expected_path=None,
                runtime_document_id=None,
                document_name=None,
            ):
                calls.append((
                    handle,
                    reference,
                    expected_path,
                    runtime_document_id,
                    document_name,
                ))
                return ScreenRectangle(100, 200, 180, 230)

        manager = SelectionOverlayManager(
            locator=Locator(),
            backend=FakeBackend(),
        )
        status = manager.update(
            {
                "app_type": "excel",
                "file_path": "",
                "document_name": "통합 문서1",
                "window_handle": 10,
                "identity_kind": "runtime",
                "runtime_document_id": "RUNTIME-10",
            },
            {"selection_kind": "range", "selection_reference": "E9"},
        )

        self.assertTrue(status["visible"])
        self.assertEqual(
            (10, "E9", "", "RUNTIME-10", "통합 문서1"),
            calls[-1],
        )

    def test_switching_workbooks_hides_same_address_until_new_lookup(self):
        release = threading.Event()

        class Locator:
            def __init__(self):
                self.paths = []

            def locate(
                self,
                handle,
                reference,
                expected_path=None,
                runtime_document_id=None,
                document_name=None,
            ):
                self.paths.append(expected_path)
                if expected_path.endswith("second.xlsx"):
                    release.wait(1)
                return ScreenRectangle(100, 200, 180, 230)

        locator = Locator()
        backend = FakeBackend()
        manager = SelectionOverlayManager(locator=locator, backend=backend)
        context = {"selection_kind": "range", "selection_reference": "A1"}
        first = {
            "app_type": "excel",
            "window_handle": 10,
            "file_path": r"C:\work\first.xlsx",
        }
        second = {
            "app_type": "excel",
            "window_handle": 10,
            "file_path": r"C:\work\second.xlsx",
        }

        self.assertTrue(manager.update(first, context)["visible"])
        hidden_before = backend.hidden
        scheduled = manager.schedule(second, context)
        self.assertEqual("scheduled", scheduled["status"])
        self.assertGreater(backend.hidden, hidden_before)
        release.set()
        manager.close()


if __name__ == "__main__":
    unittest.main()
