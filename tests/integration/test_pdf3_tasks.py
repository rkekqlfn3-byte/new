import json
import tempfile
import unittest

from engine.parser import CommandParser
from engine.pdf import PdfIntakeManager, PdfTaskService
from tests.fixtures.pdf_factory import create_owned_pdf_fixtures

OWNED_CREDENTIAL = "-".join(("owned", "test", "credential"))


class FakeConfig:
    def __init__(self, *, provider="openai", api_key=None):
        self.provider = provider
        self.api_key = OWNED_CREDENTIAL if api_key is None else api_key

    def get_ai_config(self):
        return {"provider": self.provider, "api_key": self.api_key}


class FakeLlm:
    def __init__(self):
        self.calls = []

    def _invoke_provider(
        self,
        provider,
        api_key,
        prompt,
        user_input,
        image_data,
        mode,
        stream_callback,
    ):
        self.calls.append(
            {
                "provider": provider,
                "api_key": api_key,
                "prompt": prompt,
                "user_input": user_input,
                "image_data": image_data,
                "mode": mode,
                "stream_callback": stream_callback,
            }
        )
        return {"response": "첫 페이지의 핵심 내용을 확인했습니다. [p.1]"}


class FakeOfficeWorkflow:
    def __init__(self):
        self.prepared = []
        self.executed = []

    def prepare(self, _connection, output_kinds):
        outputs = tuple(output_kinds)
        self.prepared.append(outputs)
        return {
            "output_kinds": list(outputs),
            "output_names": [f"result.{kind}" for kind in outputs],
            "output_paths": {kind: f"C:/owned/result.{kind}" for kind in outputs},
            "recipe": [],
        }

    def execute(self, _connection, plan, **kwargs):
        self.executed.append((plan, kwargs))
        return {
            "outputs": {
                kind: {"verification": {"verified": True}}
                for kind in plan["output_kinds"]
            },
            "output_names": list(plan["output_names"]),
            "recipe": [],
        }


class Pdf3TaskIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-pdf3-")
        self.addCleanup(self.temporary.cleanup)
        self.fixtures = create_owned_pdf_fixtures(self.temporary.name)
        self.manager = PdfIntakeManager()
        self.parser = CommandParser(pdf_intake_manager=self.manager)

    def _attach_fake_ai(self, *, api_key=None, office=None):
        llm = FakeLlm()
        self.parser.pdf_task_service = PdfTaskService(
            self.manager,
            llm,
            FakeConfig(api_key=api_key),
            office_workflow=office,
        )
        return llm

    def test_local_search_returns_page_citations_and_content_free_evidence(self):
        self.manager.connect_file(str(self.fixtures.text))

        result = self.parser.execute_command_result(
            '이 PDF에서 "Page Two" 찾아줘',
            session_id="pdf3-search",
        )
        evidence = json.dumps(result["data"], ensure_ascii=False)

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual("pdf_search", result["action"])
        self.assertIn("p.2", result["message"])
        self.assertEqual("session_only", result["data"]["chat_persistence"])
        self.assertNotIn("Jarvis PDF Page Two", evidence)
        self.assertNotIn(str(self.fixtures.text), evidence)

    def test_local_table_preview_requires_one_high_confidence_table(self):
        self.manager.connect_file(str(self.fixtures.table))

        result = self.parser.execute_command_result(
            "이 PDF에서 표만 추출해줘",
            session_id="pdf3-table",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertIn("Item | Amount", result["message"])
        self.assertEqual(2, result["data"]["table"]["column_count"])
        self.assertEqual(2, result["data"]["table"]["row_count"])

    def test_summary_discloses_transfer_then_runs_once_after_confirmation(self):
        self.manager.connect_file(str(self.fixtures.text))
        llm = self._attach_fake_ai()

        waiting = self.parser.execute_command_result(
            "이 PDF 1페이지 요약해줘",
            session_id="pdf3-summary",
        )
        public = json.dumps(waiting, ensure_ascii=False)

        self.assertEqual("confirmation_required", waiting["status"])
        self.assertIn("OPENAI", waiting["message"])
        self.assertIn("1페이지", waiting["message"])
        self.assertIn("전송 글자 수", waiting["message"])
        self.assertNotIn("Jarvis PDF Page One", public)
        self.assertNotIn(str(self.fixtures.text), public)
        self.assertEqual([], llm.calls)

        confirmation = waiting["data"]["confirmation"]
        completed = self.parser.resolve_pending_confirmation(
            "pdf3-summary",
            confirmation_id=confirmation["confirmation_id"],
            option_id="continue",
        )

        self.assertTrue(completed["success"])
        self.assertTrue(completed["verified"])
        self.assertEqual("pdf_summary", completed["action"])
        self.assertIn("p.1", completed["message"])
        self.assertEqual("session_only", completed["data"]["chat_persistence"])
        self.assertEqual(1, len(llm.calls))
        self.assertIn("[p.1]", llm.calls[0]["user_input"])

    def test_missing_api_key_blocks_before_confirmation(self):
        self.manager.connect_file(str(self.fixtures.text))
        self._attach_fake_ai(api_key="")

        result = self.parser.execute_command_result(
            "이 PDF 1페이지 설명해줘",
            session_id="pdf3-no-key",
        )

        self.assertFalse(result["success"])
        self.assertEqual("clarification_required", result["status"])
        self.assertEqual("pdf_ai_key_missing", result["data"]["pdf_error"]["code"])

    def test_cancelling_external_transfer_keeps_provider_call_count_zero(self):
        self.manager.connect_file(str(self.fixtures.text))
        llm = self._attach_fake_ai()
        waiting = self.parser.execute_command_result(
            "이 PDF 1페이지 요약해줘",
            session_id="pdf3-cancel",
        )

        confirmation = waiting["data"]["confirmation"]
        cancelled = self.parser.resolve_pending_confirmation(
            "pdf3-cancel",
            confirmation_id=confirmation["confirmation_id"],
            option_id="cancel",
        )

        self.assertFalse(cancelled["success"])
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual([], llm.calls)

    def test_changed_connection_invalidates_approved_transfer(self):
        self.manager.connect_file(str(self.fixtures.text))
        llm = self._attach_fake_ai()
        waiting = self.parser.execute_command_result(
            "이 PDF 1페이지 요약해줘",
            session_id="pdf3-changed",
        )
        self.manager.connect_file(str(self.fixtures.table))

        confirmation = waiting["data"]["confirmation"]
        completed = self.parser.resolve_pending_confirmation(
            "pdf3-changed",
            confirmation_id=confirmation["confirmation_id"],
            option_id="continue",
        )

        self.assertFalse(completed["success"])
        self.assertEqual("pdf_context_changed", completed["data"]["pdf_error"]["code"])
        self.assertEqual([], llm.calls)

    def test_report_creates_only_declared_office_outputs_after_ai_consent(self):
        self.manager.connect_file(str(self.fixtures.text))
        office = FakeOfficeWorkflow()
        llm = self._attach_fake_ai(office=office)

        waiting = self.parser.execute_command_result(
            "이 PDF 1페이지로 보고서랑 발표자료 만들어줘",
            session_id="pdf3-report",
        )

        self.assertEqual("confirmation_required", waiting["status"])
        self.assertIn("result.word", waiting["message"])
        self.assertIn("result.powerpoint", waiting["message"])
        self.assertEqual([("word", "powerpoint")], office.prepared)
        self.assertEqual([], office.executed)

        confirmation = waiting["data"]["confirmation"]
        completed = self.parser.resolve_pending_confirmation(
            "pdf3-report",
            confirmation_id=confirmation["confirmation_id"],
            option_id="continue",
        )

        self.assertTrue(completed["success"])
        self.assertTrue(completed["verified"])
        self.assertEqual("pdf_report", completed["action"])
        self.assertEqual(1, len(llm.calls))
        self.assertEqual(1, len(office.executed))
        self.assertEqual((1,), office.executed[0][1]["citation_pages"])

    def test_pdf_table_to_excel_never_calls_external_ai(self):
        self.manager.connect_file(str(self.fixtures.table))
        office = FakeOfficeWorkflow()
        llm = self._attach_fake_ai(office=office)

        waiting = self.parser.execute_command_result(
            "이 PDF 표를 엑셀로 추출해줘",
            session_id="pdf3-excel",
        )
        self.assertEqual("confirmation_required", waiting["status"])
        self.assertIn("result.excel", waiting["message"])
        self.assertEqual([], llm.calls)

        confirmation = waiting["data"]["confirmation"]
        completed = self.parser.resolve_pending_confirmation(
            "pdf3-excel",
            confirmation_id=confirmation["confirmation_id"],
            option_id="continue",
        )

        self.assertTrue(completed["success"])
        self.assertEqual("pdf_table_to_excel", completed["action"])
        self.assertEqual([], llm.calls)
        self.assertEqual(1, len(office.executed))


if __name__ == "__main__":
    unittest.main()
