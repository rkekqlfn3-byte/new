import hashlib
import tempfile
import unittest

from engine.pdf import (
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfExtractionResult,
    PdfPageText,
    PdfReadError,
    PdfReadErrorCode,
    chunk_pdf_pages,
    citation_label,
    find_pdf_headings,
    find_pdf_tables,
    read_pdf_document,
)
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures


def extraction_for_pages(*texts):
    fingerprint = hashlib.sha256("|".join(texts).encode("utf-8")).hexdigest()
    document = PdfDocumentSnapshot(
        document_id="pdf-analysis-owned",
        document_fingerprint=fingerprint,
        page_count=len(texts),
        file_size_bytes=sum(len(text.encode("utf-8")) for text in texts),
        encrypted=False,
        document_kind=PdfDocumentKind.TEXT,
        captured_at="2026-08-04T09:10:00+09:00",
    )
    pages = tuple(PdfPageText.from_text(index, text) for index, text in enumerate(texts, 1))
    return PdfExtractionResult(
        document=document,
        pages=pages,
        requested_pages=tuple(range(1, len(texts) + 1)),
    )


class PdfAnalysisTests(unittest.TestCase):
    def test_page_chunks_never_become_one_15000_character_blob(self):
        extraction = extraction_for_pages("A" * 7_000, "B" * 7_000, "C" * 7_000)

        chunks = chunk_pdf_pages(extraction, max_chunk_chars=2_000)

        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(chunk.text) <= 2_000 for chunk in chunks))
        self.assertEqual({1, 2, 3}, {chunk.page_number for chunk in chunks})
        self.assertTrue(all("text" not in chunk.to_evidence_dict() for chunk in chunks))

    def test_textless_selected_page_requires_ocr_instead_of_silent_omission(self):
        extraction = extraction_for_pages("Readable", "")

        with self.assertRaises(PdfReadError) as caught:
            chunk_pdf_pages(extraction)

        self.assertEqual(PdfReadErrorCode.OCR_REQUIRED, caught.exception.code)
        self.assertEqual(2, caught.exception.page_number)

    def test_heading_candidates_keep_source_pages(self):
        extraction = extraction_for_pages(
            "1. Overview\nThis is a complete sentence.",
            "2 Results\nAnother complete sentence.",
        )

        headings = find_pdf_headings(extraction)

        self.assertEqual(
            [(1, "1. Overview"), (2, "2 Results")],
            [(item.page_number, item.text) for item in headings],
        )
        self.assertTrue(all("text" not in item.to_evidence_dict() for item in headings))

    def test_owned_table_fixture_is_high_confidence_and_page_grounded(self):
        with tempfile.TemporaryDirectory(prefix="jarvis-pdf-analysis-") as temp_dir:
            fixture = create_owned_pdf_fixtures(temp_dir).table
            extraction = read_pdf_document(str(fixture))

        tables = find_pdf_tables(extraction)

        self.assertEqual(1, len(tables))
        self.assertEqual(("Item", "Amount"), tables[0].headers)
        self.assertEqual((("Alpha", "10"), ("Beta", "20")), tables[0].rows)
        self.assertGreaterEqual(tables[0].confidence, 0.9)
        self.assertEqual(1, tables[0].page_number)

    def test_inconsistent_prose_is_not_claimed_as_a_table(self):
        extraction = extraction_for_pages(
            "This is prose\nA much longer sentence with several words\nEnd"
        )

        self.assertEqual((), find_pdf_tables(extraction))

    def test_citation_label_is_sorted_and_deduplicated(self):
        self.assertEqual("근거 페이지: p.1, p.3", citation_label([3, 1, 3]))


if __name__ == "__main__":
    unittest.main()
