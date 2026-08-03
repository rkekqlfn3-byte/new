import hashlib
import json
import unittest

from engine.pdf import (
    PdfContractError,
    PdfDocumentKind,
    PdfDocumentSnapshot,
    PdfExtractionMethod,
    PdfExtractionResult,
    PdfPageText,
)

DOCUMENT_FINGERPRINT = hashlib.sha256(b"owned-pdf-document").hexdigest()


def snapshot(**overrides):
    values = {
        "document_id": "pdf-owned-1",
        "document_fingerprint": DOCUMENT_FINGERPRINT,
        "page_count": 2,
        "file_size_bytes": 1_024,
        "encrypted": False,
        "document_kind": PdfDocumentKind.TEXT,
        "captured_at": "2026-08-04T09:00:00+09:00",
    }
    values.update(overrides)
    return PdfDocumentSnapshot(**values)


class PdfContractTests(unittest.TestCase):
    def test_snapshot_round_trip_is_json_serializable_and_path_free(self):
        original = snapshot()

        encoded = json.dumps(original.to_dict(), ensure_ascii=False)
        restored = PdfDocumentSnapshot.from_dict(json.loads(encoded))

        self.assertEqual(original, restored)
        self.assertNotIn("path", encoded.casefold())
        self.assertNotIn("content", encoded.casefold())

    def test_snapshot_rejects_invalid_fingerprint(self):
        with self.assertRaisesRegex(PdfContractError, "SHA-256"):
            snapshot(document_fingerprint="not-a-fingerprint")

    def test_snapshot_rejects_timestamp_without_timezone(self):
        with self.assertRaisesRegex(PdfContractError, "timezone"):
            snapshot(captured_at="2026-08-04T09:00:00")

    def test_page_factory_binds_count_and_fingerprint_to_text(self):
        page = PdfPageText.from_text(1, "Owned Jarvis PDF text")

        self.assertEqual(len(page.text), page.character_count)
        self.assertEqual(
            hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
            page.text_fingerprint,
        )
        self.assertEqual(PdfExtractionMethod.PYPDF, page.extraction_method)

    def test_page_rejects_mismatched_text_fingerprint(self):
        with self.assertRaisesRegex(PdfContractError, "fingerprint does not match"):
            PdfPageText(
                page_number=1,
                text="Owned text",
                extraction_method="pypdf",
                character_count=10,
                text_fingerprint=hashlib.sha256(b"different").hexdigest(),
            )

    def test_none_extraction_cannot_claim_text(self):
        with self.assertRaisesRegex(PdfContractError, "cannot contain text"):
            PdfPageText.from_text(1, "claimed text", extraction_method="none")

    def test_extraction_result_requires_exact_requested_pages(self):
        page = PdfPageText.from_text(1, "Page one")

        with self.assertRaisesRegex(PdfContractError, "do not match"):
            PdfExtractionResult(
                document=snapshot(),
                pages=(page,),
                requested_pages=(1, 2),
            )

    def test_extraction_result_evidence_excludes_page_text(self):
        secret = "private owned fixture content"
        pages = (
            PdfPageText.from_text(1, secret),
            PdfPageText.from_text(2, "second page"),
        )
        result = PdfExtractionResult(
            document=snapshot(),
            pages=pages,
            requested_pages=(1, 2),
            warnings=("layout_uncertain",),
        )

        runtime_json = json.dumps(result.to_dict(), ensure_ascii=False)
        evidence_json = json.dumps(result.to_evidence_dict(), ensure_ascii=False)

        self.assertIn(secret, runtime_json)
        self.assertNotIn(secret, evidence_json)
        self.assertTrue(all("text" not in item for item in result.to_evidence_dict()["pages"]))
        self.assertEqual(f"{secret}\nsecond page", result.combined_text)

    def test_warnings_accept_only_content_free_identifiers(self):
        with self.assertRaisesRegex(PdfContractError, "identifiers"):
            PdfPageText.from_text(
                1,
                "text",
                warnings=("contains private sentence",),
            )


if __name__ == "__main__":
    unittest.main()
