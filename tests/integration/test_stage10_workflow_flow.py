import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.edit_mode import EditModeController, EditSessionManager
from engine.parser import CommandParser
from engine.workflows import WorkflowExecutor
from engine.workflows.business_workflow import file_fingerprint
from engine.learning import UserPreferenceLearningManager


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()


class Intake:
    def connect_file(self, file_path):
        path = Path(file_path).resolve()
        return {
            "app_type": "excel",
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": 100,
            "active_container": "매출",
            "selection_reference": "A1:B3",
        }


class NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class Context:
    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": "excel",
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": "A" * 64,
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-17T12:00:00+09:00",
            "active_container": "매출",
            "selection_reference": "A1:B3",
            "selection_kind": "range",
            "target": {"sheet_name": "매출", "address": "A1:B3"},
            "selected_text_preview": "",
            "selected_text_length": 0,
            "selected_text_digest": digest(""),
            "cursor_reference": "A1",
        }


class Registry:
    def get(self, target):
        if str(target).casefold() != "excel":
            raise AssertionError(target)
        return object()  # Stage 10 does not invoke a cell-edit adapter.


class Analyzer:
    def __init__(self):
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return {
            "title": "매출 분석",
            "metrics": [{
                "name": "매출", "count": 2, "sum": 300,
                "average": 150, "minimum": 100, "maximum": 200,
            }],
            "tables": [{
                "name": "매출", "headers": ["지역", "매출"],
                "rows": [["서울", 100], ["부산", 200]],
                "total_rows": 2, "included_rows": 2,
            }],
            "charts": [],
            "insights": ["부산 매출이 가장 높습니다."],
            "source_files": [context["source_path"]],
        }


class Writer:
    def __init__(self, fail_times=0, slide_count=None):
        self.fail_times = fail_times
        self.slide_count = slide_count
        self.calls = 0

    def run(self, context, work_product):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("simulated Office failure")
        path = Path(context["output_path"])
        path.write_bytes(path.suffix.encode("ascii"))
        verification = {"exists": True, "format": path.suffix[1:]}
        if self.slide_count is not None:
            verification["slide_count"] = self.slide_count
        return {
            "path": str(path),
            "fingerprint": file_fingerprint(path),
            "verification": verification,
        }


class Stage10WorkflowFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "매출.xlsx"
        self.source.write_bytes(b"stage10-excel-fixture")
        self.analyzer = Analyzer()
        self.word = Writer()
        self.hwp = Writer()
        self.ppt = Writer(fail_times=1, slide_count=5)
        executor = WorkflowExecutor(
            self.root / "workflow-state",
            analyzer=self.analyzer,
            word_writer=self.word,
            hwp_writer=self.hwp,
            powerpoint_writer=self.ppt,
        )
        self.executor = executor
        self.controller = EditModeController(
            intake_manager=Intake(),
            session_manager=EditSessionManager(),
            layout_manager=NoLayout(),
            context_manager=Context(),
            native_action_registry=Registry(),
            workflow_executor=executor,
            user_learning_manager=UserPreferenceLearningManager(
                self.root / "user-style-preferences.json"
            ),
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.session = self.controller.connect_file(str(self.source))

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(self, text, request_id):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id="stage10-chat",
            edit_context={
                "edit_session_id": self.session["session_id"],
                "document_fingerprint": self.session["document_fingerprint"],
                "context_fingerprint": "A" * 64,
                "request_id": request_id,
            },
        )

    def approve(self, result):
        confirmation = result["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            "stage10-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

    def cancel(self, result):
        confirmation = result["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            "stage10-chat",
            confirmation_id=confirmation["confirmation_id"],
            option_id="cancel",
        )

    def test_cancelled_preview_creates_no_state_and_is_not_resumable(self):
        preview = self.command(
            "이 엑셀을 분석해서 보고서와 7장짜리 PPT 만들어줘.",
            "stage10-cancel-preview",
        )
        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))

        cancelled = self.cancel(preview)
        self.assertEqual("user_cancelled", cancelled["error_type"])
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))
        self.assertIsNone(self.executor.latest_for_source(self.source))

        resume = self.command("실패한 워크플로 이어서", "stage10-no-resume")
        self.assertFalse(resume["success"])
        self.assertEqual("blocked", resume["status"])

    def test_preview_failure_and_resume_from_only_failed_step(self):
        preview = self.command(
            "이 엑셀을 분석해서 보고서와 5장짜리 PPT 만들어줘.",
            "stage10-create",
        )
        self.assertEqual("confirmation_required", preview["status"])
        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(0, self.ppt.calls)
        self.assertEqual([], list(self.executor.store_dir.glob("*.json")))

        failed = self.approve(preview)
        self.assertFalse(failed["success"])
        self.assertIn("create_powerpoint_summary", failed["message"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(1, self.ppt.calls)

        cancelled_retry = self.command(
            "실패한 워크플로 이어서", "stage10-resume-cancel"
        )
        self.assertEqual("confirmation_required", cancelled_retry["status"])
        self.assertEqual(
            "user_cancelled", self.cancel(cancelled_retry)["error_type"]
        )
        self.assertEqual(
            "failed", self.executor.latest_for_source(self.source)["status"]
        )

        retry = self.command("실패한 워크플로 이어서", "stage10-resume")
        self.assertEqual("confirmation_required", retry["status"])
        completed = self.approve(retry)
        self.assertTrue(completed["success"])
        self.assertTrue(completed["verified"])
        self.assertEqual("resume_business_workflow", completed["data"]["operation"])
        observations = completed["data"]["observations"]
        self.assertEqual(2, len(observations["created_files"]))
        self.assertEqual(5, observations["verification_results"]["create_powerpoint_summary"]["slide_count"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(2, self.ppt.calls)
        self.assertFalse(completed["data"]["undo_available"])
        self.assertEqual("ready", self.controller.status()["session"]["state"])

    def test_explicit_hwp_report_is_previewed_and_completed_without_word(self):
        self.ppt.fail_times = 0
        preview = self.command(
            "이 엑셀을 분석해서 한글 보고서와 5장짜리 PPT 만들어줘.",
            "stage10-hwp-create",
        )

        self.assertEqual("confirmation_required", preview["status"])
        self.assertIn("한글", preview["message"])
        completed = self.approve(preview)

        self.assertTrue(completed["success"])
        observations = completed["data"]["observations"]
        self.assertEqual("hwp", observations["report_format"])
        self.assertEqual(".hwp", Path(observations["output_paths"]["report"]).suffix)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(1, self.hwp.calls)
        self.assertEqual(1, self.ppt.calls)
        self.assertIn("한글 보고서", completed["message"])


if __name__ == "__main__":
    unittest.main()
