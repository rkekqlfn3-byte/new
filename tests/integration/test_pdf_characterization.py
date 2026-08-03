"""Characterize the pre-existing Jarvis PDF reader with owned fixtures."""

import tempfile
import unittest

from engine.document_reader import extract_text
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures


class PdfReaderCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-owned-pdf-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)

    def test_fixture_set_is_complete_and_caller_owned(self):
        self.assertEqual(
            {
                "text",
                "scanned",
                "encrypted",
                "corrupt",
                "rotated",
                "table",
                "mixed",
                "compressed",
            },
            set(self.fixtures.as_dict()),
        )
        for path in self.fixtures.as_dict().values():
            self.assertTrue(path.is_file())
            self.assertTrue(path.is_relative_to(self.temporary.name))

    def test_existing_reader_extracts_all_text_pages(self):
        extracted = extract_text(str(self.fixtures.text))

        self.assertIn("Jarvis PDF Page One", extracted)
        self.assertIn("Jarvis PDF Page Two", extracted)

    def test_existing_reader_returns_empty_text_for_image_only_pdf(self):
        self.assertEqual("", extract_text(str(self.fixtures.scanned)))

    def test_existing_reader_preserves_rotated_page_text(self):
        self.assertIn("Rotated Jarvis Page", extract_text(str(self.fixtures.rotated)))

    def test_existing_reader_extracts_table_tokens_without_claiming_structure(self):
        extracted = extract_text(str(self.fixtures.table))

        self.assertIn("Item Amount", extracted)
        self.assertIn("Alpha 10", extracted)
        self.assertIn("Beta 20", extracted)

    def test_existing_reader_returns_typed_text_failure_for_encrypted_pdf(self):
        result = extract_text(str(self.fixtures.encrypted))

        self.assertIn("파일 읽기 실패", result)
        self.assertIn(".pdf", result)

    def test_existing_reader_returns_typed_text_failure_for_corrupt_pdf(self):
        result = extract_text(str(self.fixtures.corrupt))

        self.assertIn("파일 읽기 실패", result)
        self.assertIn(".pdf", result)


if __name__ == "__main__":
    unittest.main()
