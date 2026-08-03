import hashlib
import json
import unittest

from engine.pdf import (
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfExtractionResult,
    PdfPageText,
    PdfReadError,
    PdfReadErrorCode,
    PdfSearchMatch,
    PdfSearchResult,
    search_pdf_text,
)


def extraction(*texts):
    pages = tuple(PdfPageText.from_text(index, text) for index, text in enumerate(texts, 1))
    snapshot = PdfDocumentSnapshot(
        document_id="pdf-search-owned",
        document_fingerprint=hashlib.sha256(b"pdf-search-owned").hexdigest(),
        page_count=len(pages),
        file_size_bytes=512,
        encrypted=False,
        document_kind=PdfDocumentKind.TEXT,
        captured_at="2026-08-04T09:00:00+09:00",
    )
    return PdfExtractionResult(
        document=snapshot,
        pages=pages,
        requested_pages=tuple(range(1, len(pages) + 1)),
    )


class PdfSearchTests(unittest.TestCase):
    def test_search_returns_page_offsets_and_context(self):
        source = extraction(
            "첫 페이지 계약 기간은 12개월입니다.", "계약 해지는 두 번째 페이지입니다."
        )

        result = search_pdf_text(source, "계약", context_chars=8)

        self.assertEqual((1, 2), tuple(item.page_number for item in result.matches))
        self.assertTrue(all(item.end_offset > item.start_offset for item in result.matches))
        self.assertTrue(all("계약" in item.excerpt for item in result.matches))
        self.assertFalse(result.truncated)

    def test_search_is_case_insensitive_by_default(self):
        source = extraction("Jarvis jarvis JARVIS")

        self.assertEqual(3, len(search_pdf_text(source, "jarvis").matches))
        self.assertEqual(
            1,
            len(search_pdf_text(source, "Jarvis", case_sensitive=True).matches),
        )

    def test_search_treats_regex_characters_literally(self):
        source = extraction("A+B and AB")

        result = search_pdf_text(source, "A+B")

        self.assertEqual(1, len(result.matches))
        self.assertEqual("A+B", result.matches[0].excerpt[:3])

    def test_search_limit_marks_result_truncated(self):
        source = extraction("x x x x")

        result = search_pdf_text(source, "x", max_matches=2)

        self.assertEqual(2, len(result.matches))
        self.assertTrue(result.truncated)

    def test_search_evidence_excludes_query_and_excerpt(self):
        query = "private query"
        source = extraction(f"before {query} after")

        result = search_pdf_text(source, query)
        encoded = json.dumps(result.to_evidence_dict(), ensure_ascii=False)

        self.assertNotIn(query, encoded)
        self.assertNotIn(result.matches[0].excerpt, encoded)
        self.assertIn("query_fingerprint", encoded)
        self.assertIn("excerpt_fingerprint", encoded)

    def test_invalid_query_has_typed_failure(self):
        with self.assertRaises(PdfReadError) as caught:
            search_pdf_text(extraction("text"), "")

        self.assertEqual(PdfReadErrorCode.SEARCH_QUERY_INVALID, caught.exception.code)

    def test_search_result_rejects_match_outside_searched_pages(self):
        source = extraction("one")
        match = PdfSearchMatch(2, 0, 1, "x")

        with self.assertRaisesRegex(ValueError, "outside"):
            PdfSearchResult(
                document=source.document,
                query="x",
                searched_pages=(1,),
                matches=(match,),
                case_sensitive=False,
                truncated=False,
            )


if __name__ == "__main__":
    unittest.main()
