import tempfile
import unittest
from pathlib import Path

from engine.workflows import (
    PowerPointSummaryWriter,
    WordReportWriter,
    WorkProductData,
    WorkflowExecutionError,
    WorkflowExecutor,
)


def product(source):
    return {
        "title": "매출 분석",
        "metrics": [
            {
                "name": "매출",
                "count": 2,
                "sum": 300,
                "average": 150,
                "minimum": 100,
                "maximum": 200,
            }
        ],
        "tables": [
            {
                "name": "매출",
                "headers": ["지역", "매출"],
                "rows": [["서울", 100], ["부산", 200]],
                "total_rows": 2,
                "included_rows": 2,
            }
        ],
        "charts": [],
        "insights": ["부산 매출이 가장 높습니다."],
        "source_files": [str(source)],
    }


class FakeAnalyzer:
    def __init__(self):
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return product(context["source_path"])


class FakeWriter:
    def __init__(self, label, fail_times=0, slides=None):
        self.label = label
        self.fail_times = fail_times
        self.slides = slides
        self.calls = 0

    def run(self, context, work_product):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"{self.label} temporary failure")
        path = Path(context["output_path"])
        if path.exists():
            raise AssertionError("writer must never overwrite an existing artifact")
        path.write_bytes((self.label + "-verified").encode("utf-8"))
        from engine.workflows.business_workflow import file_fingerprint

        verification = {"exists": True, "format": path.suffix.lstrip(".")}
        if self.slides is not None:
            verification["slide_count"] = self.slides
        return {
            "path": str(path),
            "fingerprint": file_fingerprint(path),
            "verification": verification,
        }


class Stage10WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "매출.xlsx"
        self.source.write_bytes(b"owned-excel-fixture")
        self.analyzer = FakeAnalyzer()
        self.word = FakeWriter("word")
        self.ppt = FakeWriter("ppt", fail_times=1, slides=5)
        self.executor = WorkflowExecutor(
            self.root / "state",
            analyzer=self.analyzer,
            word_writer=self.word,
            powerpoint_writer=self.ppt,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_common_business_data_contract_is_json_bounded(self):
        value = WorkProductData.from_value(product(self.source))
        self.assertEqual("매출 분석", value.title)
        self.assertEqual(300, value.to_dict()["metrics"][0]["sum"])
        with self.assertRaisesRegex(Exception, "metrics"):
            WorkProductData(
                title="too many",
                metrics=[{}] * 51,
                tables=[],
                charts=[],
                insights=[],
                source_files=[],
            )

    def test_approval_prepare_does_not_create_outputs(self):
        state = self.executor.prepare(self.source)
        self.assertEqual("approval_required", state["status"])
        self.assertEqual([], state["created_files"])
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())
        self.assertFalse(Path(state["output_paths"]["report"]).exists())
        self.assertFalse(Path(state["output_paths"]["presentation"]).exists())

    def test_failure_resumes_only_failed_step_without_duplicate_artifacts(self):
        state = self.executor.prepare(self.source)
        with self.assertRaises(WorkflowExecutionError) as captured:
            self.executor.start(state)
        self.assertEqual("create_powerpoint_summary", captured.exception.step)
        self.assertEqual(
            "create_powerpoint_summary", captured.exception.failed_step
        )
        self.assertTrue(captured.exception.retryable)
        self.assertRegex(
            captured.exception.diagnostic_context["workflow_hash"],
            r"^[A-F0-9]{64}$",
        )
        failed = self.executor.load(state["workflow_id"])
        report = Path(failed["output_paths"]["report"])
        self.assertTrue(report.is_file())
        original_report = report.read_bytes()
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(1, self.ppt.calls)

        completed = self.executor.run(state["workflow_id"])
        self.assertTrue(completed["verified"])
        self.assertEqual(3, len(completed["successful_steps"]))
        self.assertEqual(2, len(completed["created_files"]))
        self.assertEqual(original_report, report.read_bytes())
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(2, self.ppt.calls)

        repeated = self.executor.run(state["workflow_id"])
        self.assertFalse(repeated["changed"])
        self.assertEqual(1, self.word.calls)
        self.assertEqual(2, self.ppt.calls)

    def test_missing_verified_artifact_restarts_at_that_step(self):
        state = self.executor.prepare(self.source)
        with self.assertRaises(WorkflowExecutionError):
            self.executor.start(state)
        failed = self.executor.load(state["workflow_id"])
        Path(failed["output_paths"]["report"]).unlink()

        completed = self.executor.run(state["workflow_id"])
        self.assertTrue(completed["verified"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(2, self.word.calls)
        self.assertEqual(2, self.ppt.calls)

    def test_changed_source_blocks_resume_before_any_new_step(self):
        state = self.executor.prepare(self.source)
        self.source.write_bytes(b"user-changed-source")
        with self.assertRaisesRegex(Exception, "원본 파일이 바뀌어"):
            self.executor.start(state)
        self.assertEqual(0, self.analyzer.calls)
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_existing_output_names_are_never_overwritten(self):
        first_report = self.root / "매출_JARVIS_보고서.docx"
        first_ppt = self.root / "매출_JARVIS_5장_요약.pptx"
        first_report.write_bytes(b"user-report")
        first_ppt.write_bytes(b"user-ppt")
        state = self.executor.prepare(self.source)
        self.assertNotEqual(first_report, Path(state["output_paths"]["report"]))
        self.assertNotEqual(first_ppt, Path(state["output_paths"]["presentation"]))
        self.assertEqual(b"user-report", first_report.read_bytes())
        self.assertEqual(b"user-ppt", first_ppt.read_bytes())

    def test_planned_presentation_name_matches_requested_slide_count(self):
        for slide_count in (3, 5, 7):
            with self.subTest(slide_count=slide_count):
                state = self.executor.prepare(self.source, slide_count=slide_count)
                name = Path(state["output_paths"]["presentation"]).name
                self.assertIn(f"_{slide_count}장_요약", name)
                self.assertEqual(slide_count, state["slide_count"])

    def test_approval_revalidates_output_collision_before_persisting(self):
        state = self.executor.prepare(self.source)
        collision = Path(state["output_paths"]["report"])
        collision.write_bytes(b"user-created-after-preview")

        with self.assertRaisesRegex(Exception, "같은 이름의 파일"):
            self.executor.start(state)

        self.assertEqual(b"user-created-after-preview", collision.read_bytes())
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_legacy_preview_is_never_resumable_and_retention_removes_it(self):
        state = self.executor.prepare(self.source)
        self.executor._save(state)
        self.assertIsNone(self.executor.latest_for_source(self.source))
        self.assertEqual(1, self.executor.cleanup_stale_previews(max_age_days=0))
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_approved_style_defaults_change_generated_content_without_code_objects(self):
        value = WorkProductData.from_value(product(self.source))
        report = WordReportWriter._report_text(value, {
            "report_tone": "friendly",
            "number_format": "currency_krw",
            "table_style": "header_bold",
        })
        self.assertIn("핵심 내용을 정리했습니다", report)
        self.assertIn("₩300", report)
        self.assertIn("[머리글]", report)
        self.assertIn(self.source.name, report)
        self.assertNotIn(str(self.source.parent), report)
        slides = PowerPointSummaryWriter._slide_content(
            value, {"number_format": "thousands"}, slide_count=7
        )
        self.assertEqual(7, len(slides))
        self.assertEqual("결론 및 다음 단계", slides[-1][0])


if __name__ == "__main__":
    unittest.main()
