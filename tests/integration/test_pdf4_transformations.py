import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from engine.parser import CommandParser
from engine.pdf import PdfIntakeManager, PdfTaskService, PdfTransformationService
from engine.pdf.reader import read_pdf_document
from tests.fixtures.pdf_factory import write_text_pdf


class Pdf4TransformationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf4-flow-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = write_text_pdf(
            self.root / "first.pdf", ["First One", "First Two", "First Three"]
        )
        self.second = write_text_pdf(self.root / "second.pdf", ["Second One"])
        self.manager = PdfIntakeManager()
        self.manager.connect_file(str(self.first))
        self.parser = CommandParser(pdf_intake_manager=self.manager)
        transformations = PdfTransformationService(
            self.manager,
            merge_picker=lambda: (str(self.second),),
        )
        self.parser.pdf_task_service = PdfTaskService(
            self.manager,
            self.parser.llm_engine,
            self.parser.dict_mgr,
            transformation_service=transformations,
        )

    def _approve(self, waiting, session_id):
        confirmation = waiting["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            session_id,
            confirmation_id=confirmation["confirmation_id"],
            option_id="continue",
        )

    def test_split_confirmation_creates_no_file_then_verified_output(self):
        before = set(self.root.iterdir())
        waiting = self.parser.execute_command_result(
            "이 PDF 2~3페이지만 분할해줘", session_id="pdf4-split"
        )
        public = json.dumps(waiting, ensure_ascii=False)

        self.assertEqual("confirmation_required", waiting["status"])
        self.assertIn("추출할 페이지: 2-3", waiting["message"])
        self.assertEqual(before, set(self.root.iterdir()))
        self.assertNotIn(str(self.root), public)
        completed = self._approve(waiting, "pdf4-split")
        output = self.root / completed["data"]["pdf_file_action"]["output_name"]
        self.assertTrue(completed["success"])
        self.assertTrue(completed["verified"])
        self.assertEqual(2, read_pdf_document(output).document.page_count)

    def test_merge_preview_and_output_preserve_source_order(self):
        waiting = self.parser.execute_command_result(
            "이 PDF와 다른 PDF 병합해줘", session_id="pdf4-merge"
        )

        self.assertIn("1. first.pdf", waiting["message"])
        self.assertIn("2. second.pdf", waiting["message"])
        completed = self._approve(waiting, "pdf4-merge")
        output = self.root / completed["data"]["pdf_file_action"]["output_name"]
        pages = read_pdf_document(output).pages
        self.assertIn("First One", pages[0].text)
        self.assertIn("Second One", pages[-1].text)

    def test_rotation_preview_discloses_target_and_direction(self):
        waiting = self.parser.execute_command_result(
            "이 PDF 2페이지를 왼쪽으로 회전해줘", session_id="pdf4-rotate"
        )

        self.assertIn("회전할 페이지: 2", waiting["message"])
        self.assertIn("시계 방향 270도", waiting["message"])
        completed = self._approve(waiting, "pdf4-rotate")
        output = self.root / completed["data"]["pdf_file_action"]["output_name"]
        self.assertEqual(270, int(PdfReader(output).pages[1].rotation or 0))

    def test_verified_output_can_be_undone_after_separate_confirmation(self):
        waiting = self.parser.execute_command_result(
            "이 PDF 1페이지만 분할해줘", session_id="pdf4-create"
        )
        created = self._approve(waiting, "pdf4-create")
        output = self.root / created["data"]["pdf_file_action"]["output_name"]

        undo_waiting = self.parser.execute_command_result(
            "방금 만든 PDF 취소해줘", session_id="pdf4-undo"
        )
        self.assertTrue(output.exists())
        self.assertEqual("confirmation_required", undo_waiting["status"])
        undone = self._approve(undo_waiting, "pdf4-undo")

        self.assertTrue(undone["success"])
        self.assertEqual("pdf_undo", undone["action"])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
