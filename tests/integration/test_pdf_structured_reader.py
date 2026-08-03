import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pypdf import PdfWriter

from engine.pdf import (
    PdfDocumentKind,
    PdfExtractionMethod,
    PdfReadError,
    PdfReadErrorCode,
    PdfReadLimits,
    read_pdf_document,
)
from tests.fixtures.pdf_factory import (
    create_owned_pdf_fixtures,
    write_asciihex_text_pdf,
    write_compressed_text_pdf,
)


class PdfStructuredReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf-reader-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)

    def assert_error_code(self, path, code, **kwargs):
        with self.assertRaises(PdfReadError) as caught:
            read_pdf_document(path, **kwargs)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_full_text_read_has_page_provenance_and_file_fingerprint(self):
        result = read_pdf_document(self.fixtures.text)

        expected_hash = hashlib.sha256(self.fixtures.text.read_bytes()).hexdigest()
        self.assertEqual(expected_hash, result.document.document_fingerprint)
        self.assertEqual(PdfDocumentKind.TEXT, result.document.document_kind)
        self.assertEqual((1, 2), result.requested_pages)
        self.assertIn("Jarvis PDF Page One", result.pages[0].text)
        self.assertIn("Jarvis PDF Page Two", result.pages[1].text)

    def test_single_page_read_keeps_document_count_and_marks_kind_unknown(self):
        result = read_pdf_document(self.fixtures.text, pages="2")

        self.assertEqual(2, result.document.page_count)
        self.assertEqual((2,), result.requested_pages)
        self.assertEqual(PdfDocumentKind.UNKNOWN, result.document.document_kind)
        self.assertEqual(("partial_document_kind_unknown",), result.warnings)

    def test_scanned_pdf_is_explicit_not_silent_empty_text(self):
        result = read_pdf_document(self.fixtures.scanned)

        self.assertEqual(PdfDocumentKind.SCANNED, result.document.document_kind)
        self.assertEqual(PdfExtractionMethod.NONE, result.pages[0].extraction_method)
        self.assertEqual(("no_text_layer",), result.pages[0].warnings)
        self.assertEqual(("ocr_required",), result.warnings)

    def test_mixed_pdf_classifies_text_and_image_pages(self):
        result = read_pdf_document(self.fixtures.mixed)

        self.assertEqual(PdfDocumentKind.MIXED, result.document.document_kind)
        self.assertTrue(result.pages[0].text)
        self.assertFalse(result.pages[1].text)
        self.assertEqual(("partial_text_layer",), result.warnings)

    def test_common_flate_compressed_content_is_supported(self):
        result = read_pdf_document(self.fixtures.compressed)

        self.assertIn("Compressed Jarvis Page", result.combined_text)

    def test_compressed_content_expansion_is_bounded_before_extraction(self):
        compressed = Path(self.temporary.name) / "compressed-large.pdf"
        write_compressed_text_pdf(compressed, "A" * 5_000)

        self.assert_error_code(
            compressed,
            PdfReadErrorCode.CONTENT_STREAM_TOO_LARGE,
            limits=PdfReadLimits(max_page_stream_bytes=256),
        )

    def test_unsupported_content_filter_is_typed_and_blocked(self):
        unsupported = Path(self.temporary.name) / "unsupported-filter.pdf"
        write_asciihex_text_pdf(unsupported, "Owned unsupported filter")

        self.assert_error_code(
            unsupported,
            PdfReadErrorCode.CONTENT_FILTER_UNSUPPORTED,
        )

    def test_evidence_has_no_path_or_page_text(self):
        result = read_pdf_document(self.fixtures.text)

        encoded = json.dumps(result.to_evidence_dict(), ensure_ascii=False)

        self.assertNotIn(str(self.fixtures.text), encoded)
        self.assertNotIn("Jarvis PDF Page One", encoded)
        self.assertTrue(all("text" not in item for item in result.to_evidence_dict()["pages"]))

    def test_encrypted_and_corrupt_pdfs_have_distinct_typed_failures(self):
        encrypted = self.assert_error_code(
            self.fixtures.encrypted,
            PdfReadErrorCode.PASSWORD_REQUIRED,
        )
        self.assertEqual("needs_input", encrypted.outcome)
        self.assert_error_code(
            self.fixtures.corrupt,
            PdfReadErrorCode.MALFORMED_DOCUMENT,
        )

    def test_zero_page_pdf_is_rejected(self):
        empty = Path(self.temporary.name) / "empty.pdf"
        with empty.open("wb") as output:
            PdfWriter().write(output)

        self.assert_error_code(empty, PdfReadErrorCode.EMPTY_DOCUMENT)

    def test_file_page_text_and_stream_limits_fail_closed(self):
        cases = (
            (PdfReadLimits(max_file_bytes=1), PdfReadErrorCode.FILE_TOO_LARGE),
            (PdfReadLimits(max_pages=1), PdfReadErrorCode.PAGE_LIMIT_EXCEEDED),
            (PdfReadLimits(max_total_text_chars=5), PdfReadErrorCode.TEXT_LIMIT_EXCEEDED),
            (PdfReadLimits(max_page_text_chars=5), PdfReadErrorCode.TEXT_LIMIT_EXCEEDED),
            (
                PdfReadLimits(max_page_stream_bytes=1),
                PdfReadErrorCode.CONTENT_STREAM_TOO_LARGE,
            ),
        )
        for limits, code in cases:
            with self.subTest(code=code):
                self.assert_error_code(self.fixtures.text, code, limits=limits)

    def test_cancel_timeout_and_missing_dependency_are_typed(self):
        self.assert_error_code(
            self.fixtures.text,
            PdfReadErrorCode.CANCELLED,
            cancel_check=lambda: True,
        )
        with mock.patch("engine.pdf.reader.time.monotonic", side_effect=(0.0, 2.0)):
            self.assert_error_code(
                self.fixtures.text,
                PdfReadErrorCode.TIMEOUT,
                limits=PdfReadLimits(timeout_seconds=1.0),
            )
        with mock.patch("engine.pdf.reader.PdfReader", None):
            dependency = self.assert_error_code(
                self.fixtures.text,
                PdfReadErrorCode.DEPENDENCY_UNAVAILABLE,
            )
        self.assertEqual("environment_blocked", dependency.outcome)

    def test_relative_and_missing_paths_are_rejected_without_path_evidence(self):
        unsafe = self.assert_error_code("relative.pdf", PdfReadErrorCode.UNSAFE_PATH)
        missing = Path(self.temporary.name) / "missing.pdf"
        absent = self.assert_error_code(missing, PdfReadErrorCode.FILE_NOT_FOUND)

        self.assertNotIn("path", unsafe.to_evidence_dict())
        self.assertNotIn(str(missing), json.dumps(absent.to_evidence_dict()))

    def test_source_change_during_hashing_is_detected(self):
        changed = Path(self.temporary.name) / "changing.pdf"
        changed.write_bytes(self.fixtures.text.read_bytes())
        calls = 0

        def mutate_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                changed.write_bytes(changed.read_bytes() + b"\n")
            return False

        self.assert_error_code(
            changed,
            PdfReadErrorCode.SOURCE_CHANGED,
            cancel_check=mutate_once,
        )


if __name__ == "__main__":
    unittest.main()
