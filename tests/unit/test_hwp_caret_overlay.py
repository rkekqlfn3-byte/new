"""The 한글 caret marker.

Naming the caret in words — "3쪽 12줄 30칸" — still leaves the reader hunting
for it on the page, so the caret is marked on screen instead. These pin the
part that decides where the marker goes and when there must not be one.
"""

from __future__ import annotations

import unittest

from engine.edit_mode.hwp_caret import (
    MIN_MARKER_HEIGHT,
    MIN_MARKER_WIDTH,
    HwpCaretLocator,
)
from engine.edit_mode.selection_overlay import SelectionOverlayManager


class FakeCaretApi:
    def __init__(self, bounds=None, error=None):
        self.bounds = bounds
        self.error = error
        self.handles = []

    def caret_rectangle(self, window_handle):
        self.handles.append(window_handle)
        if self.error is not None:
            raise self.error
        return self.bounds


class FakeBackend:
    def __init__(self, shown=True):
        self.shown = shown
        self.shows = []
        self.hides = 0

    def show(self, handle, rectangle, label):
        self.shows.append((handle, rectangle, label))
        return self.shown

    def hide(self):
        self.hides += 1

    def close(self):
        pass


class HwpCaretLocatorTests(unittest.TestCase):
    def test_a_one_pixel_caret_is_grown_into_something_visible(self):
        # 한글 reports the caret as a single pixel, which no one can see.
        locator = HwpCaretLocator(FakeCaretApi((2189, 404, 2190, 405)))
        rectangle = locator.locate(4242)
        self.assertEqual(2189, rectangle.left)
        self.assertEqual(2189 + MIN_MARKER_WIDTH, rectangle.right)
        self.assertEqual(404 + MIN_MARKER_HEIGHT, rectangle.bottom)

    def test_a_caret_taller_than_the_minimum_keeps_its_own_height(self):
        locator = HwpCaretLocator(FakeCaretApi((10, 20, 12, 20 + 40)))
        rectangle = locator.locate(4242)
        self.assertEqual(60, rectangle.bottom)

    def test_no_marker_without_a_caret_or_a_window(self):
        self.assertIsNone(HwpCaretLocator(FakeCaretApi(None)).locate(4242))
        self.assertIsNone(HwpCaretLocator(FakeCaretApi((1, 1, 2, 2))).locate(0))

    def test_a_degenerate_rectangle_marks_nothing(self):
        # A caret scrolled off the page reports an inverted rectangle; marking
        # it would point at somewhere the caret is not.
        locator = HwpCaretLocator(FakeCaretApi((100, 100, 50, 50)))
        self.assertIsNone(locator.locate(4242))

    def test_a_failing_windows_call_is_not_an_error_for_the_caller(self):
        locator = HwpCaretLocator(FakeCaretApi(error=OSError("no thread")))
        self.assertIsNone(locator.locate(4242))


class HwpCaretOverlayTests(unittest.TestCase):
    def _manager(self, bounds, shown=True):
        backend = FakeBackend(shown=shown)
        manager = SelectionOverlayManager(
            backend=backend,
            caret_locator=HwpCaretLocator(FakeCaretApi(bounds)),
        )
        return manager, backend

    def test_a_hwp_session_marks_the_caret(self):
        manager, backend = self._manager((100, 200, 101, 201))
        status = manager.update({"app_type": "hwp", "window_handle": 4242}, {})
        self.assertEqual("shown", status["status"])
        self.assertTrue(status["visible"])
        handle, rectangle, label = backend.shows[-1]
        self.assertEqual(4242, handle)
        self.assertEqual(100, rectangle.left)
        self.assertEqual("커서", label)

    def test_no_caret_hides_rather_than_leaving_a_stale_marker(self):
        manager, backend = self._manager(None)
        status = manager.update({"app_type": "hwp", "window_handle": 4242}, {})
        self.assertEqual("caret_unavailable", status["status"])
        self.assertFalse(status["visible"])
        self.assertEqual([], backend.shows)

    def test_a_session_without_a_window_marks_nothing(self):
        manager, backend = self._manager((1, 1, 2, 2))
        status = manager.update({"app_type": "hwp"}, {})
        self.assertEqual("window_unavailable", status["status"])
        self.assertEqual([], backend.shows)

    def test_disabling_the_overlay_also_disables_the_caret_marker(self):
        manager, backend = self._manager((1, 1, 2, 2))
        manager.set_enabled(False)
        status = manager.update({"app_type": "hwp", "window_handle": 4242}, {})
        self.assertFalse(status["visible"])
        self.assertEqual([], backend.shows)

    def test_excel_still_takes_the_address_based_path(self):
        # The caret branch must not swallow the app it was not written for.
        manager, backend = self._manager((1, 1, 2, 2))
        status = manager.update(
            {"app_type": "excel", "window_handle": 4242}, {}
        )
        self.assertEqual("unsupported_selection", status["status"])
        self.assertEqual([], backend.shows)


class UnsavedHwpDocumentTests(unittest.TestCase):
    """A 한글 document with no name is still a document on the user's screen."""

    def _manager(self):
        from engine.edit_mode.intake import FileIntakeManager

        return FileIntakeManager.__new__(FileIntakeManager)

    def test_an_unsaved_document_connects_by_its_window(self):
        document = self._manager()._runtime_hwp_document(
            {"window_handle": 778899, "document_name": "빈 문서 1"}
        )
        self.assertEqual("HWP-WINDOW:778899", document["runtime_document_id"])
        self.assertEqual("runtime", document["identity_kind"])
        self.assertFalse(document["is_saved"])
        self.assertEqual("", document["file_path"])

    def test_two_unsaved_windows_are_different_documents(self):
        manager = self._manager()
        first = manager._runtime_hwp_document({"window_handle": 1})
        second = manager._runtime_hwp_document({"window_handle": 2})
        self.assertNotEqual(
            first["runtime_document_id"], second["runtime_document_id"]
        )

    def test_a_window_that_cannot_be_identified_is_refused(self):
        from engine.edit_mode.intake import EditDocumentOpenTimeout

        with self.assertRaises(EditDocumentOpenTimeout):
            self._manager()._runtime_hwp_document({"window_handle": 0})


if __name__ == "__main__":
    unittest.main()
