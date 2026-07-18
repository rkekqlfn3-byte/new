import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine.workflows import (
    ExcelSalesAnalyzer,
    HwpReportWriter,
    PowerPointSummaryWriter,
    WordReportWriter,
    WorkProductData,
    WorkflowExecutionError,
    WorkflowExecutor,
)
from engine.edit_mode.stage10 import StructuredWorkflowIntentAnalyzer


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


class FakeComRuntime:
    def CoInitialize(self):
        return None

    def CoUninitialize(self):
        return None


class FakeLease:
    def __init__(self):
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


class FakeUsedRange:
    def __init__(self, value, rows=None, columns=None):
        self._value = value
        self.value_reads = 0
        matrix = list(value) if isinstance(value, (tuple, list)) else []
        inferred_rows = len(matrix) or 1
        first = matrix[0] if matrix and isinstance(matrix[0], (tuple, list)) else []
        inferred_columns = len(first) or 1
        self.Rows = SimpleNamespace(Count=rows or inferred_rows)
        self.Columns = SimpleNamespace(Count=columns or inferred_columns)

    @property
    def Value2(self):
        self.value_reads += 1
        return self._value


class FakeCharts:
    Count = 0


class FakeWorksheet:
    def __init__(self, name, value, *, visible=-1, rows=None, columns=None):
        self.Name = name
        self.Visible = visible
        self.UsedRange = FakeUsedRange(value, rows=rows, columns=columns)

    def ChartObjects(self):
        return FakeCharts()


class FakeWorksheets:
    def __init__(self, worksheets):
        self._worksheets = list(worksheets)
        self.Count = len(self._worksheets)

    def Item(self, index):
        return self._worksheets[index - 1]


class FakeWorkbook:
    def __init__(self, path, worksheets):
        self.FullName = str(path)
        self.Worksheets = FakeWorksheets(worksheets)


class FakeHwpParameter:
    def __init__(self, **values):
        self.HSet = self
        for key, value in values.items():
            setattr(self, key, value)


class FakeHwpAction:
    def __init__(self, hwp):
        self.hwp = hwp

    def GetDefault(self, name, parameter):
        if name == "CharShape":
            parameter.Bold = self.hwp.bold
            parameter.Height = self.hwp.height
        elif name == "ParagraphShape":
            parameter.AlignType = self.hwp.alignment
        return True

    def Execute(self, name, parameter):
        if name == "InsertText":
            self.hwp.text += str(parameter.Text)
            return True
        if name == "CharShape":
            self.hwp.bold = int(parameter.Bold)
            self.hwp.height = int(parameter.Height)
            return True
        return False

    def Run(self, name):
        alignments = {
            "ParagraphShapeAlignJustify": 0,
            "ParagraphShapeAlignLeft": 1,
            "ParagraphShapeAlignRight": 2,
            "ParagraphShapeAlignCenter": 3,
        }
        if name in alignments:
            self.hwp.alignment = alignments[name]
        return True


class FakeHwpApplication:
    def __init__(self):
        self.text = ""
        self.bold = 0
        self.height = 1000
        self.alignment = 1
        self.cleared = False
        self.quit_called = False
        self.HParameterSet = SimpleNamespace(
            HInsertText=FakeHwpParameter(Text=""),
            HCharShape=FakeHwpParameter(Bold=0, Height=1000),
            HParaShape=FakeHwpParameter(AlignType=1),
        )
        self.HAction = FakeHwpAction(self)

    def PointToHwpUnit(self, value):
        return int(round(float(value) * 100))

    def GetTextFile(self, _format, _option):
        return self.text

    def SaveAs(self, path, format_name, _options):
        if format_name != "HWP":
            return False
        Path(path).write_bytes(b"owned-hwp-report")
        return True

    def Clear(self, _option):
        self.cleared = True

    def Quit(self):
        self.quit_called = True


class Stage10WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "매출.xlsx"
        self.source.write_bytes(b"owned-excel-fixture")
        self.analyzer = FakeAnalyzer()
        self.word = FakeWriter("word")
        self.hwp = FakeWriter("hwp")
        self.ppt = FakeWriter("ppt", fail_times=1, slides=5)
        self.executor = WorkflowExecutor(
            self.root / "state",
            analyzer=self.analyzer,
            word_writer=self.word,
            hwp_writer=self.hwp,
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

    def _analyze_sheets(self, worksheets):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, worksheets)
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)
        result = analyzer.run({
            "source_path": str(self.source),
            "title": "다중 시트 분석",
            "preferences": {"summary_lines": 8},
        })
        self.assertTrue(lease.cleaned)
        return result

    def test_excel_analyzer_builds_one_bounded_table_per_visible_sheet(self):
        result = self._analyze_sheets([
            FakeWorksheet("매출", (
                ("지역", "금액"), ("서울", 100), ("부산", 200),
            )),
            FakeWorksheet("비용", (
                ("항목", "금액"), ("인건비", 70), ("임대료", 30),
            )),
            FakeWorksheet("숨김", (("비밀",), (999,)), visible=0),
        ])

        self.assertEqual(["매출", "비용"], [table["name"] for table in result["tables"]])
        self.assertEqual(2, len(result["tables"]))
        self.assertEqual(2, result["tables"][0]["included_rows"])
        self.assertEqual(6, result["tables"][0]["used_cells"])
        metric_names = [metric["name"] for metric in result["metrics"]]
        self.assertIn("매출/금액", metric_names)
        self.assertIn("비용/금액", metric_names)
        self.assertTrue(all("숨김" not in name for name in metric_names))
        slides = PowerPointSummaryWriter._slide_content(
            WorkProductData.from_value(result), slide_count=5
        )
        self.assertIn("분석 시트: 2개 (매출, 비용)", slides[2][1])
        self.assertIn("전체 데이터 행: 4", slides[2][1])

    def test_excel_analyzer_rejects_one_oversized_sheet_before_reading_values(self):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("대용량", "값", rows=2001, columns=25),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        with self.assertRaisesRegex(Exception, "대용량.*50,000셀"):
            analyzer.run({"source_path": str(self.source), "preferences": {}})
        self.assertTrue(lease.cleaned)

    def test_excel_analyzer_rejects_total_visible_range_over_workbook_limit(self):
        lease = FakeLease()
        worksheets = [
            FakeWorksheet(name, "값", rows=2000, columns=25)
            for name in ("시트1", "시트2", "시트3")
        ]
        workbook = FakeWorkbook(self.source, worksheets)
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        with self.assertRaisesRegex(Exception, "전체 분석 범위는 100,000셀"):
            analyzer.run({"source_path": str(self.source), "preferences": {}})
        self.assertTrue(lease.cleaned)
        self.assertEqual(0, sum(sheet.UsedRange.value_reads for sheet in worksheets))

    def test_excel_analyzer_rejects_more_than_twenty_visible_sheets_before_read(self):
        lease = FakeLease()
        worksheets = [
            FakeWorksheet(f"시트{index}", (("값",), (index,)))
            for index in range(1, 22)
        ]
        workbook = FakeWorkbook(self.source, worksheets)
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        with self.assertRaisesRegex(Exception, "표시 시트는 20개까지"):
            analyzer.run({"source_path": str(self.source), "preferences": {}})
        self.assertTrue(lease.cleaned)
        self.assertEqual(0, sum(sheet.UsedRange.value_reads for sheet in worksheets))

    def test_excel_analyzer_caps_combined_preview_rows_at_five_hundred(self):
        worksheets = [
            FakeWorksheet(
                f"시트{sheet_index}",
                tuple([("순번", "값")] + [
                    (row_index, sheet_index * 1000 + row_index)
                    for row_index in range(1, 102)
                ]),
            )
            for sheet_index in range(1, 7)
        ]

        result = self._analyze_sheets(worksheets)

        included = [table["included_rows"] for table in result["tables"]]
        self.assertEqual([100, 100, 100, 100, 100, 0], included)
        self.assertEqual(500, sum(included))

    def test_approval_prepare_does_not_create_outputs(self):
        state = self.executor.prepare(self.source)
        self.assertEqual("approval_required", state["status"])
        self.assertEqual([], state["created_files"])
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())
        self.assertFalse(Path(state["output_paths"]["report"]).exists())
        self.assertFalse(Path(state["output_paths"]["presentation"]).exists())

    def test_hwp_report_plan_uses_hwp_writer_without_calling_word(self):
        analyzer = FakeAnalyzer()
        word = FakeWriter("word")
        hwp = FakeWriter("hwp")
        powerpoint = FakeWriter("ppt", slides=5)
        executor = WorkflowExecutor(
            self.root / "hwp-state",
            analyzer=analyzer,
            word_writer=word,
            hwp_writer=hwp,
            powerpoint_writer=powerpoint,
        )
        state = executor.prepare(self.source, report_format="hwp")

        self.assertEqual("hwp", state["report_format"])
        self.assertEqual(".hwp", Path(state["output_paths"]["report"]).suffix)
        result = executor.start(state)

        self.assertTrue(result["verified"])
        self.assertEqual("hwp", result["report_format"])
        self.assertEqual(0, word.calls)
        self.assertEqual(1, hwp.calls)
        self.assertEqual(1, powerpoint.calls)

    def test_hwp_writer_inserts_formats_reads_and_saves_owned_report(self):
        application = FakeHwpApplication()
        writer = HwpReportWriter(
            application_factory=lambda program_id: application,
            com_runtime=FakeComRuntime(),
        )
        output = self.root / "분석_보고서.hwp"

        artifact = writer.run({
            "output_path": str(output),
            "preferences": {
                "emphasis_style": "bold",
                "font_scale": "larger",
                "paragraph_align": "center",
            },
        }, product(self.source))

        self.assertTrue(output.is_file())
        self.assertEqual("hwp", artifact["verification"]["format"])
        self.assertTrue(artifact["verification"]["content_readback"])
        self.assertEqual(1, application.bold)
        self.assertEqual(1400, application.height)
        self.assertEqual(3, application.alignment)
        self.assertTrue(application.cleared)
        self.assertTrue(application.quit_called)

    def test_workflow_intent_selects_hwp_word_default_and_both_formats(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        intent = analyzer.analyze(
            "이 엑셀 분석해서 한글 보고서와 6장짜리 PPT 만들어줘",
            {"app_type": "excel"},
        )

        self.assertEqual("hwp", intent.params["report_format"])
        self.assertEqual(6, intent.params["slide_count"])
        self.assertIn("한글 보고서", intent.description)
        generic = analyzer.analyze(
            "이 엑셀 분석해서 보고서와 PPT 만들어줘",
            {"app_type": "excel"},
        )
        self.assertEqual("word", generic.params["report_format"])
        both = analyzer.analyze(
            "이 엑셀 분석해서 Word와 한글 보고서랑 PPT 만들어줘",
            {"app_type": "excel"},
        )
        self.assertEqual("both", both.params["report_format"])
        self.assertIn("Word·한글 보고서", both.description)

    def test_both_report_plan_runs_each_report_as_a_separate_verified_step(self):
        analyzer = FakeAnalyzer()
        word = FakeWriter("word")
        hwp = FakeWriter("hwp")
        powerpoint = FakeWriter("ppt", slides=5)
        executor = WorkflowExecutor(
            self.root / "both-state",
            analyzer=analyzer,
            word_writer=word,
            hwp_writer=hwp,
            powerpoint_writer=powerpoint,
        )
        state = executor.prepare(self.source, report_format="both")

        self.assertEqual(
            [
                "analyze_excel",
                "create_word_report",
                "create_hwp_report",
                "create_powerpoint_summary",
            ],
            state["step_order"],
        )
        self.assertEqual(
            {"report_word", "report_hwp", "presentation"},
            set(state["output_paths"]),
        )
        self.assertEqual(
            ".docx", Path(state["output_paths"]["report_word"]).suffix
        )
        self.assertEqual(
            ".hwp", Path(state["output_paths"]["report_hwp"]).suffix
        )

        result = executor.start(state)

        self.assertTrue(result["verified"])
        self.assertEqual(["word", "hwp"], result["report_formats"])
        self.assertEqual(4, len(result["successful_steps"]))
        self.assertEqual(3, len(result["created_files"]))
        self.assertEqual(1, analyzer.calls)
        self.assertEqual(1, word.calls)
        self.assertEqual(1, hwp.calls)
        self.assertEqual(1, powerpoint.calls)

    def test_both_report_resume_keeps_verified_word_when_hwp_failed(self):
        analyzer = FakeAnalyzer()
        word = FakeWriter("word")
        hwp = FakeWriter("hwp", fail_times=1)
        powerpoint = FakeWriter("ppt", slides=5)
        executor = WorkflowExecutor(
            self.root / "both-resume-state",
            analyzer=analyzer,
            word_writer=word,
            hwp_writer=hwp,
            powerpoint_writer=powerpoint,
        )
        state = executor.prepare(self.source, report_format="both")

        with self.assertRaises(WorkflowExecutionError) as captured:
            executor.start(state)

        self.assertEqual("create_hwp_report", captured.exception.failed_step)
        failed = executor.load(state["workflow_id"])
        word_path = Path(failed["output_paths"]["report_word"])
        self.assertTrue(word_path.is_file())
        word_bytes = word_path.read_bytes()
        self.assertFalse(Path(failed["output_paths"]["report_hwp"]).exists())
        self.assertEqual(0, powerpoint.calls)

        completed = executor.run(state["workflow_id"])

        self.assertTrue(completed["verified"])
        self.assertEqual(word_bytes, word_path.read_bytes())
        self.assertEqual(1, analyzer.calls)
        self.assertEqual(1, word.calls)
        self.assertEqual(2, hwp.calls)
        self.assertEqual(1, powerpoint.calls)
        self.assertEqual(3, len(completed["created_files"]))

    def test_schema_two_hwp_step_migrates_to_explicit_hwp_step(self):
        state = self.executor.prepare(self.source, report_format="hwp")
        state["schema_version"] = 2
        state.pop("step_order")
        state["steps"]["create_word_report"] = state["steps"].pop(
            "create_hwp_report"
        )
        state["steps"]["create_word_report"]["status"] = "failed"
        state["current_step"] = "create_word_report"
        state["failed_step"] = "create_word_report"
        state["verification_results"]["create_word_report"] = {"format": "hwp"}
        self.executor._save(state)

        migrated = self.executor.load(state["workflow_id"])

        self.assertEqual(3, migrated["schema_version"])
        self.assertEqual(2, migrated["migrated_from_schema"])
        self.assertEqual(
            [
                "analyze_excel",
                "create_hwp_report",
                "create_powerpoint_summary",
            ],
            migrated["step_order"],
        )
        self.assertIn("create_hwp_report", migrated["steps"])
        self.assertNotIn("create_word_report", migrated["steps"])
        self.assertEqual("create_hwp_report", migrated["current_step"])
        self.assertEqual("create_hwp_report", migrated["failed_step"])
        self.assertEqual(
            {"format": "hwp"},
            migrated["verification_results"]["create_hwp_report"],
        )

    def test_both_approval_rejects_reordered_steps_and_hwp_collision(self):
        reordered = self.executor.prepare(self.source, report_format="both")
        reordered["step_order"][1:3] = reversed(reordered["step_order"][1:3])

        with self.assertRaisesRegex(Exception, "단계 구성이 올바르지"):
            self.executor.start(reordered)

        collision_plan = self.executor.prepare(self.source, report_format="both")
        collision = Path(collision_plan["output_paths"]["report_hwp"])
        collision.write_bytes(b"user-created-after-preview")

        with self.assertRaisesRegex(Exception, "같은 이름의 파일"):
            self.executor.start(collision_plan)

        self.assertEqual(b"user-created-after-preview", collision.read_bytes())
        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(0, self.hwp.calls)
        self.assertFalse(
            self.executor._path(collision_plan["workflow_id"]).exists()
        )

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

    def test_approval_rejects_tampered_report_format_before_any_step(self):
        state = self.executor.prepare(self.source, report_format="word")
        state["report_format"] = "hwp"

        with self.assertRaisesRegex(Exception, "단계 구성이 올바르지"):
            self.executor.start(state)

        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(0, self.hwp.calls)
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
        hwp_slides = PowerPointSummaryWriter._slide_content(
            value, slide_count=5, report_label="한글"
        )
        self.assertIn("한글 보고서", hwp_slides[-1][1])

    def test_approved_word_formatting_defaults_are_applied_and_read_back(self):
        content = SimpleNamespace(
            Font=SimpleNamespace(Bold=0, Size=11.0),
            ParagraphFormat=SimpleNamespace(Alignment=0),
        )

        applied = WordReportWriter._apply_formatting_preferences(content, {
            "emphasis_style": "bold",
            "font_scale": "larger",
            "paragraph_align": "center",
        })

        self.assertEqual(-1, content.Font.Bold)
        self.assertEqual(14.0, content.Font.Size)
        self.assertEqual(1, content.ParagraphFormat.Alignment)
        self.assertEqual({
            "emphasis_style": "bold",
            "font_scale": "larger",
            "paragraph_align": "center",
        }, applied)

    def test_invalid_word_formatting_default_is_blocked(self):
        content = SimpleNamespace(
            Font=SimpleNamespace(Bold=0, Size=11.0),
            ParagraphFormat=SimpleNamespace(Alignment=0),
        )

        with self.assertRaisesRegex(Exception, "정렬 기본값"):
            WordReportWriter._apply_formatting_preferences(
                content, {"paragraph_align": "diagonal"}
            )

    def test_workflow_plan_accepts_only_bounded_word_formatting_defaults(self):
        state = self.executor.prepare(self.source, preferences={
            "emphasis_style": "regular",
            "font_scale": "smaller",
            "paragraph_align": "justify",
        })

        self.assertEqual(
            "smaller", state["applied_preferences"]["font_scale"]
        )

    def test_approval_rejects_tampered_formatting_default_before_any_step(self):
        state = self.executor.prepare(
            self.source, preferences={"paragraph_align": "center"}
        )
        state["applied_preferences"]["paragraph_align"] = "diagonal"

        with self.assertRaisesRegex(Exception, "학습 기본값이 바뀌어"):
            self.executor.start(state)

        self.assertEqual(0, self.analyzer.calls)
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())


if __name__ == "__main__":
    unittest.main()
