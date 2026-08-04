import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = PROJECT_ROOT / "gui"


class PdfConnectionUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (GUI_DIR / "index.html").read_text(encoding="utf-8")
        cls.css = (GUI_DIR / "css" / "chat.css").read_text(encoding="utf-8")
        cls.script = (GUI_DIR / "js" / "pdf_context.js").read_text(encoding="utf-8")

    def test_index_ships_explicit_read_only_connection_controls(self):
        self.assertIn('id="pdf-context-bar"', self.html)
        self.assertIn("PDF · 읽기 전용", self.html)
        self.assertIn('id="btn-pdf-connect"', self.html)
        self.assertIn('id="btn-pdf-disconnect"', self.html)
        self.assertIn('id="pdf-file-drop-zone"', self.html)
        self.assertIn('src="js/pdf_context.js"', self.html)

    def test_ui_uses_picker_status_and_token_bound_disconnect_endpoints(self):
        self.assertIn("eel.choose_and_connect_pdf_document()()", self.script)
        self.assertIn("eel.get_pdf_connection_status()()", self.script)
        self.assertIn("eel.disconnect_pdf_document(connectionId)()", self.script)
        self.assertIn("eel.connect_dropped_pdf_document(", self.script)
        self.assertNotIn("eel.connect_pdf_document", self.script)

    def test_connection_payload_is_validated_before_rendering(self):
        self.assertIn("connection.schema_version !== 1", self.script)
        self.assertIn("connection.read_only !== true", self.script)
        self.assertIn("connection.connection_id", self.script)
        self.assertIn("connection.document_fingerprint", self.script)
        self.assertIn("Number.isInteger(pages)", self.script)
        self.assertIn("['unknown', 'text', 'scanned', 'mixed']", self.script)

    def test_untrusted_document_name_is_rendered_as_plain_text(self):
        self.assertIn(
            "pdfDocumentName.textContent = validConnection.document_name",
            self.script,
        )
        self.assertNotIn("innerHTML", self.script)
        self.assertNotIn("file_path", self.script)

    def test_compact_context_bar_has_truncation_and_read_only_visual_boundary(self):
        self.assertIn(".pdf-context-bar {", self.css)
        self.assertIn(".pdf-context-badge {", self.css)
        self.assertIn(".pdf-file-drop-zone {", self.css)
        self.assertIn("text-overflow: ellipsis", self.css)
        self.assertIn("white-space: nowrap", self.css)


if __name__ == "__main__":
    unittest.main()
