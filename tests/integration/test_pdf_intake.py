import tempfile
import unittest
from pathlib import Path

from engine.pdf import (
    PdfConnectionError,
    PdfIntakeManager,
    PdfReadError,
    PdfReadErrorCode,
    PdfReferenceError,
)
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures


class PdfIntakeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf-intake-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)
        self.manager = PdfIntakeManager(file_picker=lambda: str(self.fixtures.text))

    def test_connect_status_and_disconnect_expose_no_runtime_path(self):
        connection = self.manager.choose_and_connect()

        self.assertTrue(connection["read_only"])
        self.assertEqual(2, connection["page_count"])
        self.assertNotIn("file_path", connection)
        self.assertEqual(connection, self.manager.status()["connection"])

        disconnected = self.manager.disconnect(connection["connection_id"])
        self.assertEqual(connection["connection_id"], disconnected["connection_id"])
        self.assertFalse(self.manager.status()["connected"])

    def test_cancelled_picker_does_not_replace_current_connection(self):
        current = self.manager.connect_file(str(self.fixtures.text))
        self.manager._file_picker = lambda: None

        self.assertIsNone(self.manager.choose_and_connect())
        self.assertEqual(current, self.manager.status()["connection"])

    def test_dropped_pdf_requires_exact_path_name_and_size(self):
        path = self.fixtures.text

        connection = self.manager.connect_dropped(
            path.name,
            path.stat().st_size,
            str(path),
        )

        self.assertEqual(path.name, connection["document_name"])
        self.assertNotIn("file_path", connection)

    def test_drop_without_browser_path_does_not_guess_by_file_name(self):
        with self.assertRaises(PdfConnectionError) as caught:
            self.manager.connect_dropped(
                self.fixtures.text.name,
                self.fixtures.text.stat().st_size,
                None,
            )

        self.assertEqual("pdf_drop_path_unavailable", caught.exception.code)
        self.assertFalse(self.manager.status()["connected"])

    def test_drop_identity_mismatch_preserves_existing_connection(self):
        current = self.manager.connect_file(str(self.fixtures.text))

        with self.assertRaises(PdfConnectionError) as caught:
            self.manager.connect_dropped(
                self.fixtures.table.name,
                self.fixtures.table.stat().st_size + 1,
                str(self.fixtures.table),
            )

        self.assertEqual("pdf_drop_identity_mismatch", caught.exception.code)
        self.assertEqual(current, self.manager.status()["connection"])

    def test_wrong_disconnect_token_is_blocked(self):
        self.manager.connect_file(str(self.fixtures.text))

        with self.assertRaises(PdfConnectionError) as caught:
            self.manager.disconnect("pdf-connection-" + "f" * 32)

        self.assertEqual("pdf_connection_mismatch", caught.exception.code)
        self.assertTrue(self.manager.status()["connected"])

    def test_read_current_revalidates_identity_and_page_range(self):
        self.manager.connect_file(str(self.fixtures.text))

        result = self.manager.read_current(pages="2")

        self.assertEqual((2,), result.requested_pages)
        self.assertIn("Page Two", result.combined_text)

    def test_search_records_bounded_last_search_reference(self):
        self.manager.connect_file(str(self.fixtures.text))

        search = self.manager.search_current("Page")
        reference = self.manager.resolve_reference("방금 찾은 부분만 알려줘")

        self.assertEqual(2, len(search.matches))
        self.assertEqual((1, 2), reference.page_numbers)
        self.assertEqual("last_search", reference.source)

    def test_replacing_connection_clears_last_search(self):
        self.manager.connect_file(str(self.fixtures.text))
        self.manager.search_current("Page")
        self.manager.connect_file(str(self.fixtures.table))

        with self.assertRaises(PdfReferenceError) as caught:
            self.manager.resolve_reference("방금 찾은 부분 알려줘")

        self.assertEqual("last_search_missing", caught.exception.code)

    def test_source_change_disconnects_stale_connection(self):
        connected_path = Path(self.temporary.name) / "mutable.pdf"
        connected_path.write_bytes(self.fixtures.text.read_bytes())
        self.manager.connect_file(str(connected_path))
        connected_path.write_bytes(connected_path.read_bytes() + b"\n")

        with self.assertRaises(PdfReadError) as caught:
            self.manager.current(revalidate=True)

        self.assertEqual(PdfReadErrorCode.SOURCE_CHANGED, caught.exception.code)
        self.assertFalse(self.manager.status()["connected"])

    def test_no_connection_never_falls_back_to_an_open_office_document(self):
        with self.assertRaises(PdfConnectionError) as caught:
            self.manager.resolve_reference("이거 요약해줘")

        self.assertEqual("pdf_not_connected", caught.exception.code)

    def test_last_explicit_reference_supports_here_and_adjacent_page(self):
        self.manager.connect_file(str(self.fixtures.text))

        first = self.manager.resolve_reference("1페이지 설명해줘")
        here = self.manager.resolve_reference("여기 다시 설명해줘")
        following = self.manager.resolve_reference("다음 페이지 설명해줘")

        self.assertEqual((1,), first.page_numbers)
        self.assertEqual((1,), here.page_numbers)
        self.assertEqual((2,), following.page_numbers)


if __name__ == "__main__":
    unittest.main()
