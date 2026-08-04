import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from engine.pdf import PdfIntakeManager
from engine.pdf.reader import read_pdf_document
from engine.pdf.transformation_service import (
    PdfTransformationError,
    PdfTransformationService,
)
from engine.pdf.transformation_verification import (
    PdfTransformationVerificationError,
    verify_pdf_output,
)
from tests.fixtures.pdf_factory import write_text_pdf


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PdfTransformationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf4-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = write_text_pdf(
            self.root / "first.pdf",
            ["First Page One", "First Page Two", "First Page Three"],
        )
        self.second = write_text_pdf(
            self.root / "second.pdf", ["Second Page One", "Second Page Two"]
        )
        self.manager = PdfIntakeManager()
        self.manager.connect_file(str(self.first))
        self.service = PdfTransformationService(
            self.manager,
            merge_picker=lambda: (str(self.second),),
        )

    def _request(self, command):
        return self.manager.resolve_command(command)

    def test_extract_creates_nothing_before_execution_and_reopens_result(self):
        source_fingerprint = _sha256(self.first)
        payload = self.service.prepare(self._request("이 PDF 2~3페이지만 분할해줘"))
        output = self.root / payload["output_name"]

        self.assertFalse(output.exists())
        result = self.service.execute(payload)
        extraction = read_pdf_document(output)

        self.assertEqual(2, extraction.document.page_count)
        self.assertIn("First Page Two", extraction.pages[0].text)
        self.assertIn("First Page Three", extraction.pages[1].text)
        self.assertEqual(source_fingerprint, _sha256(self.first))
        self.assertTrue(result["source_files_unchanged"])
        self.assertTrue(result["verification"]["page_order_verified"])

    def test_rotate_preserves_all_pages_and_changes_only_selected_rotation(self):
        payload = self.service.prepare(
            self._request("이 PDF 2페이지를 오른쪽으로 회전해줘")
        )

        result = self.service.execute(payload)
        output = self.root / result["output_name"]
        rotations = tuple(int(page.rotation or 0) for page in PdfReader(output).pages)

        self.assertEqual((0, 90, 0), rotations)
        self.assertEqual(3, result["verification"]["page_count"])

    def test_merge_preserves_approved_input_order(self):
        payload = self.service.prepare(self._request("이 PDF와 다른 PDF 병합해줘"))

        result = self.service.execute(payload)
        pages = read_pdf_document(self.root / result["output_name"]).pages

        self.assertEqual(5, len(pages))
        self.assertIn("First Page One", pages[0].text)
        self.assertIn("First Page Three", pages[2].text)
        self.assertIn("Second Page One", pages[3].text)
        self.assertIn("Second Page Two", pages[4].text)

    def test_split_without_page_subset_and_rotate_without_direction_are_blocked(self):
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(PdfTransformationError, "페이지 범위"):
            self.service.prepare(self._request("이 PDF 분할해줘"))
        with self.assertRaisesRegex(PdfTransformationError, "회전 방향"):
            self.service.prepare(self._request("이 PDF 2페이지를 회전해줘"))

        self.assertEqual(before, set(self.root.iterdir()))

    def test_output_name_race_preserves_existing_file(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        output = self.root / payload["output_name"]
        output.write_bytes(b"existing-owned-data")

        with self.assertRaises(Exception):
            self.service.execute(payload)

        self.assertEqual(b"existing-owned-data", output.read_bytes())

    def test_source_change_after_prepare_blocks_without_partial_output(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        output = self.root / payload["output_name"]
        write_text_pdf(self.first, ["Changed Source"])

        with self.assertRaises(Exception):
            self.service.execute(payload)

        self.assertFalse(output.exists())

    def test_final_readback_failure_removes_committed_owned_output(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        output = self.root / payload["output_name"]
        calls = 0

        def fail_second(path, expected):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PdfTransformationVerificationError(
                    "injected final verification failure"
                )
            return verify_pdf_output(path, expected)

        with patch(
            "engine.pdf.transformation_service.verify_pdf_output",
            side_effect=fail_second,
        ):
            with self.assertRaisesRegex(PdfTransformationError, "검증"):
                self.service.execute(payload)

        self.assertFalse(output.exists())
        self.assertEqual([], list(self.root.glob(".jarvis_pdf_*.pdf")))

    def test_internal_write_error_is_content_free_and_removes_temp_file(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        output = self.root / payload["output_name"]

        with patch.object(
            self.service,
            "_write_action",
            side_effect=OSError(str(self.root / "private-source.pdf")),
        ):
            with self.assertRaises(PdfTransformationError) as raised:
                self.service.execute(payload)

        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertFalse(output.exists())
        self.assertEqual([], list(self.root.glob(".jarvis_pdf_*.pdf")))

    def test_planned_output_page_limit_is_checked_before_writing(self):
        before = set(self.root.iterdir())
        with patch("engine.pdf.transformation_service.MAX_PDF_PAGES", 1):
            with self.assertRaisesRegex(PdfTransformationError, "페이지 수"):
                self.service.prepare(self._request("이 PDF 1~2페이지만 분할해줘"))

        self.assertEqual(before, set(self.root.iterdir()))

    def test_undo_deletes_only_unchanged_output(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        created = self.service.execute(payload)
        output = self.root / created["output_name"]
        undo = self.service.prepare(self._request("방금 만든 PDF 취소해줘"))

        result = self.service.execute(undo)

        self.assertTrue(result["deleted"])
        self.assertFalse(output.exists())

    def test_undo_blocks_if_output_changed(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        created = self.service.execute(payload)
        output = self.root / created["output_name"]
        output.write_bytes(output.read_bytes() + b"changed")

        with self.assertRaisesRegex(PdfTransformationError, "변경"):
            self.service.prepare(self._request("방금 만든 PDF 삭제해줘"))

        self.assertTrue(output.exists())

    def test_prepared_evidence_is_content_free(self):
        payload = self.service.prepare(self._request("이 PDF 1페이지만 분할해줘"))
        action = payload["prepared_action"]
        public = json.dumps(
            {
                "operation": action["operation"],
                "source_fingerprints": action["source_fingerprints"],
                "expected_state": action["expected_state"],
            },
            ensure_ascii=False,
        )

        self.assertNotIn(str(self.root), public)
        self.assertNotIn("First Page", public)


if __name__ == "__main__":
    unittest.main()
