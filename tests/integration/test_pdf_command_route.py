import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.parser import CommandParser
from engine.pdf import PdfIntakeManager
from engine.pipeline.pdf_route import try_execute_pdf_route
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures


class PdfCommandRouteIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf-route-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)
        self.manager = PdfIntakeManager()
        self.parser = SimpleNamespace(pdf_intake_manager=self.manager)

    def test_explicit_pdf_command_without_connection_requests_connection(self):
        result = try_execute_pdf_route(self.parser, "이 PDF 요약해줘")

        self.assertFalse(result["success"])
        self.assertEqual("clarification_required", result["status"])
        self.assertEqual("pdf_not_connected", result["data"]["pdf_error"]["code"])

    def test_unrelated_command_is_not_claimed_even_when_pdf_is_connected(self):
        self.manager.connect_file(str(self.fixtures.text))

        self.assertIsNone(try_execute_pdf_route(self.parser, "계산기 열어줘"))
        self.assertIsNone(try_execute_pdf_route(self.parser, "구글에서 고양이 찾아줘"))

    def test_page_count_runs_locally_with_verified_content_free_result(self):
        connection = self.manager.connect_file(str(self.fixtures.text))

        result = try_execute_pdf_route(self.parser, "이 PDF 몇 페이지야?")
        encoded = json.dumps(result, ensure_ascii=False)

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("pdf_page_count", result["action"])
        self.assertEqual(connection["connection_id"], result["target"])
        self.assertNotIn(str(self.fixtures.text), encoded)
        self.assertNotIn("Jarvis PDF Page", encoded)

    def test_pdf3_intent_is_recognized_but_not_sent_to_generic_ai(self):
        self.manager.connect_file(str(self.fixtures.text))

        result = try_execute_pdf_route(self.parser, "이 PDF 2페이지 요약해줘")

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("pdf_summary", result["action"])
        self.assertEqual("PDF-3", result["data"]["intent"]["implementation_stage"])
        self.assertFalse(result["data"]["triage_hints"]["capability_exists"])

    def test_pdf4_mutation_is_recognized_without_creating_an_output(self):
        self.manager.connect_file(str(self.fixtures.text))
        before = {item.name for item in Path(self.temporary.name).iterdir()}

        result = try_execute_pdf_route(self.parser, "이 PDF 1페이지에서 분할해줘")
        after = {item.name for item in Path(self.temporary.name).iterdir()}

        self.assertFalse(result["success"])
        self.assertEqual("pdf_split", result["action"])
        self.assertEqual("PDF-4", result["data"]["intent"]["implementation_stage"])
        self.assertTrue(result["data"]["intent"]["requires_confirmation"])
        self.assertEqual(before, after)

    def test_pipeline_places_pdf_route_before_native_and_ai_routes(self):
        source = (
            Path(__file__).resolve().parents[2] / "engine" / "pipeline" / "command_pipeline.py"
        ).read_text(encoding="utf-8")

        pdf_route = source.index("pdf_result = try_execute_pdf_route(")
        native_route = source.index("native_result = try_execute_native_route(")
        ai_route = source.index("return execute_ai_fallback_route(")
        self.assertLess(pdf_route, native_route)
        self.assertLess(pdf_route, ai_route)

    def test_real_command_parser_uses_injected_pdf_manager(self):
        self.manager.connect_file(str(self.fixtures.text))
        parser = CommandParser(pdf_intake_manager=self.manager)

        result = parser.execute_command_result("이 PDF 몇 페이지야?")

        self.assertTrue(result["success"])
        self.assertEqual("pdf_page_count", result["action"])


if __name__ == "__main__":
    unittest.main()
