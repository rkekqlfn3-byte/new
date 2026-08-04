import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.api import pdf_api
from engine.pdf import PdfIntakeManager
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures


class PdfApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf-api-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)
        self.manager = PdfIntakeManager(file_picker=lambda: str(self.fixtures.text))
        self.previous_parser = pdf_api.parser
        pdf_api.parser = SimpleNamespace(pdf_intake_manager=self.manager)
        self.addCleanup(setattr, pdf_api, "parser", self.previous_parser)

    def test_choose_status_reference_and_disconnect_share_one_connection(self):
        connected = pdf_api.choose_and_connect_pdf_document()
        connection = connected["data"]["connection"]

        self.assertTrue(connected["success"])
        self.assertTrue(connected["verified"])
        self.assertNotIn("file_path", connection)
        status = pdf_api.get_pdf_connection_status()
        self.assertEqual(connection, status["data"]["connection"])

        reference = pdf_api.resolve_pdf_target_reference("2페이지 알려줘")
        self.assertEqual([2], reference["data"]["reference"]["page_numbers"])
        disconnected = pdf_api.disconnect_pdf_document(connection["connection_id"])
        self.assertTrue(disconnected["success"])
        self.assertFalse(pdf_api.get_pdf_connection_status()["data"]["connected"])

    def test_api_payload_never_exposes_runtime_path_or_page_text(self):
        response = pdf_api.connect_pdf_document(str(self.fixtures.text))
        encoded = json.dumps(response, ensure_ascii=False)

        self.assertNotIn(str(self.fixtures.text), encoded)
        self.assertNotIn("Jarvis PDF Page", encoded)

    def test_picker_cancel_is_typed_user_cancellation(self):
        self.manager._file_picker = lambda: None

        result = pdf_api.choose_and_connect_pdf_document()

        self.assertFalse(result["success"])
        self.assertEqual("cancelled", result["status"])
        self.assertEqual("user_cancelled", result["error_type"])

    def test_drop_without_exact_path_is_clarification_not_file_search(self):
        result = pdf_api.connect_dropped_pdf_document(
            self.fixtures.text.name,
            self.fixtures.text.stat().st_size,
            None,
        )

        self.assertFalse(result["success"])
        self.assertEqual("clarification_required", result["status"])
        self.assertEqual(
            "pdf_drop_path_unavailable",
            result["data"]["pdf_error"]["code"],
        )

    def test_encrypted_pdf_reports_needs_input_without_path(self):
        result = pdf_api.connect_pdf_document(str(self.fixtures.encrypted))
        encoded = json.dumps(result, ensure_ascii=False)

        self.assertFalse(result["success"])
        self.assertEqual("password_required", result["data"]["pdf_error"]["code"])
        self.assertEqual("needs_input", result["data"]["pdf_error"]["outcome"])
        self.assertNotIn(str(self.fixtures.encrypted), encoded)

    def test_status_revalidation_clears_a_changed_pdf(self):
        mutable = Path(self.temporary.name) / "status-change.pdf"
        mutable.write_bytes(self.fixtures.text.read_bytes())
        pdf_api.connect_pdf_document(str(mutable))
        mutable.write_bytes(mutable.read_bytes() + b"\n")

        result = pdf_api.get_pdf_connection_status()

        self.assertFalse(result["success"])
        self.assertEqual("source_changed", result["data"]["pdf_error"]["code"])
        self.assertFalse(self.manager.status()["connected"])

    def test_ambiguous_reference_is_non_terminal_clarification(self):
        pdf_api.connect_pdf_document(str(self.fixtures.text))

        result = pdf_api.resolve_pdf_target_reference("계산기 열어줘")

        self.assertFalse(result["success"])
        self.assertEqual("clarification_required", result["status"])
        self.assertIsNone(result["error_type"])
        self.assertEqual("pdf_reference_missing", result["data"]["pdf_error"]["code"])

    def test_command_resolution_exposes_no_search_query_or_document_content(self):
        pdf_api.connect_pdf_document(str(self.fixtures.text))

        result = pdf_api.resolve_pdf_command('이 PDF에서 "Page Two" 찾아줘')
        encoded = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["success"])
        self.assertEqual("search", result["data"]["request"]["intent"]["kind"])
        self.assertNotIn("Page Two", encoded)
        self.assertNotIn(str(self.fixtures.text), encoded)


if __name__ == "__main__":
    unittest.main()
