import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.pdf import (
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfReadError,
    PdfReadErrorCode,
    PdfReferenceError,
    PdfSearchMatch,
    PdfSearchResult,
)
from engine.pdf.context import PdfConnection
from engine.pdf.reference import PdfTargetReference, resolve_pdf_reference


class PdfReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf-reference-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "월간 보고서.pdf"
        self.path.write_bytes(b"%PDF-owned-reference")
        self.snapshot = PdfDocumentSnapshot(
            document_id="pdf-reference-owned",
            document_fingerprint=hashlib.sha256(b"pdf-reference-owned").hexdigest(),
            page_count=8,
            file_size_bytes=self.path.stat().st_size,
            encrypted=False,
            document_kind=PdfDocumentKind.TEXT,
            captured_at="2026-08-04T09:00:00+09:00",
        )
        self.connection = PdfConnection(
            connection_id="pdf-connection-" + "a" * 32,
            file_path=str(self.path),
            document_name=self.path.name,
            document=self.snapshot,
            connected_at=self.snapshot.captured_at,
        )

    def test_range_and_list_references_are_explicit(self):
        ranged = resolve_pdf_reference("3~5페이지 요약해줘", self.connection)
        listed = resolve_pdf_reference("1, 3, 5페이지를 비교해줘", self.connection)

        self.assertEqual((3, 4, 5), ranged.page_numbers)
        self.assertEqual((1, 3, 5), listed.page_numbers)
        self.assertEqual("explicit_pages", ranged.source)

    def test_descending_page_range_is_rejected(self):
        with self.assertRaises(PdfReadError) as caught:
            resolve_pdf_reference("5~3페이지 읽어줘", self.connection)

        self.assertEqual(PdfReadErrorCode.PAGE_SELECTION_INVALID, caught.exception.code)

    def test_connected_document_reference_selects_all_pages(self):
        for command in ("이 PDF 요약해줘", "이거 읽어줘", "현재 문서를 검색해줘"):
            with self.subTest(command=command):
                reference = resolve_pdf_reference(command, self.connection)
                self.assertEqual(tuple(range(1, 9)), reference.page_numbers)
                self.assertEqual("connected_document", reference.source)

    def test_matching_quoted_file_name_is_allowed(self):
        reference = resolve_pdf_reference(
            '"월간 보고서.pdf" 2페이지 알려줘',
            self.connection,
        )

        self.assertEqual((2,), reference.page_numbers)

    def test_different_named_pdf_requires_reconnection(self):
        with self.assertRaises(PdfReferenceError) as caught:
            resolve_pdf_reference("다른자료.pdf 요약해줘", self.connection)

        self.assertEqual("named_document_mismatch", caught.exception.code)

    def test_last_search_reference_uses_only_hit_pages(self):
        search = PdfSearchResult(
            document=self.snapshot,
            query="계약",
            searched_pages=(2, 4, 7),
            matches=(
                PdfSearchMatch(2, 0, 2, "계약"),
                PdfSearchMatch(7, 5, 7, "앞뒤 계약 문맥"),
            ),
            case_sensitive=False,
            truncated=False,
        )

        reference = resolve_pdf_reference(
            "방금 찾은 부분만 보여줘",
            self.connection,
            last_search=search,
        )

        self.assertEqual((2, 7), reference.page_numbers)
        self.assertEqual("last_search", reference.source)

    def test_missing_last_search_and_unrelated_request_need_clarification(self):
        with self.assertRaises(PdfReferenceError) as missing:
            resolve_pdf_reference("방금 찾은 부분", self.connection)
        with self.assertRaises(PdfReferenceError) as unrelated:
            resolve_pdf_reference("계산기 열어줘", self.connection)

        self.assertEqual("last_search_missing", missing.exception.code)
        self.assertEqual("pdf_reference_missing", unrelated.exception.code)

    def test_here_and_relative_pages_use_the_last_explicit_reference(self):
        anchor = resolve_pdf_reference("3~4페이지 설명해줘", self.connection)

        here = resolve_pdf_reference(
            "여기 요약해줘",
            self.connection,
            last_reference=anchor,
        )
        previous = resolve_pdf_reference(
            "앞 페이지 설명해줘",
            self.connection,
            last_reference=anchor,
        )
        following = resolve_pdf_reference(
            "다음 페이지 설명해줘",
            self.connection,
            last_reference=anchor,
        )

        self.assertEqual((3, 4), here.page_numbers)
        self.assertEqual("current_reference", here.source)
        self.assertEqual((2,), previous.page_numbers)
        self.assertEqual((5,), following.page_numbers)

    def test_here_without_an_anchor_needs_clarification(self):
        with self.assertRaises(PdfReferenceError) as caught:
            resolve_pdf_reference("여기 설명해줘", self.connection)

        self.assertEqual("current_reference_missing", caught.exception.code)

    def test_target_reference_contract_rejects_invalid_identity_and_empty_pages(self):
        with self.assertRaises(ValueError):
            PdfTargetReference("bad", "f" * 64, (1,), "explicit_pages")
        with self.assertRaises(ValueError):
            PdfTargetReference(
                "pdf-connection-" + "a" * 32,
                "f" * 64,
                (),
                "explicit_pages",
            )

    def test_public_connection_has_name_but_evidence_has_no_name_or_path(self):
        public = self.connection.to_public_dict()
        evidence = self.connection.to_evidence_dict()

        self.assertEqual(self.path.name, public["document_name"])
        self.assertEqual(self.snapshot.document_fingerprint, public["document_fingerprint"])
        self.assertNotIn("file_path", public)
        self.assertNotIn("document_name", evidence)
        self.assertNotIn("file_path", evidence)


if __name__ == "__main__":
    unittest.main()
