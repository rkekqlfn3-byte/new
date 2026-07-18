import tempfile
import unittest
import queue
import re
from pathlib import Path
from types import SimpleNamespace

from engine.workflows import (
    ExcelSalesAnalyzer,
    HwpReportWriter,
    HwpSecurityModuleUnavailable,
    HwpWorkflowTimeout,
    PowerPointSummaryWriter,
    WordReportWriter,
    WorkProductData,
    WorkflowError,
    WorkflowExecutionError,
    WorkflowExecutor,
    WorkflowJoinValidationError,
    WorkflowSourceScopeValidationError,
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


class PreflightWriter(FakeWriter):
    def __init__(self, label, *, fail_on_call=None):
        super().__init__(label)
        self.preflight_calls = 0
        self.fail_on_call = fail_on_call

    def preflight(self):
        self.preflight_calls += 1
        if self.preflight_calls == self.fail_on_call:
            raise HwpSecurityModuleUnavailable("한글 보안 모듈 준비가 필요합니다.")
        return {
            "status": "ready",
            "security_module_name": "TestSecurityModule",
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
        self._value = value
        self.UsedRange = FakeUsedRange(value, rows=rows, columns=columns)

    def Range(self, address):
        normalized = str(address).replace("$", "").upper()
        match = re.fullmatch(
            r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", normalized
        )
        if match is None:
            raise ValueError(address)

        def column_index(letters):
            value = 0
            for character in letters:
                value = value * 26 + ord(character) - ord("A") + 1
            return value - 1

        start_column = column_index(match.group(1))
        start_row = int(match.group(2)) - 1
        end_column = column_index(match.group(3) or match.group(1))
        end_row = int(match.group(4) or match.group(2)) - 1
        matrix = [list(row) for row in self._value]
        selected = tuple(
            tuple(row[start_column:end_column + 1])
            for row in matrix[start_row:end_row + 1]
        )
        return FakeUsedRange(
            selected,
            rows=end_row - start_row + 1,
            columns=end_column - start_column + 1,
        )

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

    def _analyze_sheets(
        self, worksheets, *, join_plan=None, source_scope=None
    ):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, worksheets)
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)
        result = analyzer.run({
            "source_path": str(self.source),
            "title": "다중 시트 분석",
            "preferences": {"summary_lines": 8},
            "join_plan": join_plan,
            "source_scope": source_scope,
        })
        self.assertTrue(lease.cleaned)
        return result

    @staticmethod
    def _join_plan(join_type="inner"):
        return {
            "left_sheet": "고객",
            "right_sheet": "주문",
            "left_key": "고객ID",
            "right_key": "고객ID",
            "join_type": join_type,
        }

    def test_excel_analyzer_performs_explicit_inner_join_without_raw_key_lists(self):
        source_values = (
            ("고객ID", "고객명"),
            (1, "가"),
            (2, "나"),
            (3, "다"),
        )
        order_values = (
            ("주문ID", "고객ID", "매출"),
            (101, "1", 100),
            (102, "1", 300),
            (103, "2", 200),
            (104, "4", 150),
        )

        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", source_values),
                FakeWorksheet("주문", order_values),
            ],
            join_plan=self._join_plan(),
        )

        joined = result["tables"][-1]
        self.assertTrue(joined["derived"])
        self.assertEqual("inner", joined["join"]["join_type"])
        self.assertEqual("one_to_many", joined["join"]["cardinality"])
        self.assertEqual(3, joined["join"]["output_rows"])
        self.assertEqual(
            [
                ["고객/고객ID", "고객/고객명", "주문/주문ID", "주문/매출"],
                [1, "가", 101, 100],
                [1, "가", 102, 300],
                [2, "나", 103, 200],
            ],
            [joined["headers"]] + joined["rows"],
        )
        self.assertNotIn("values", joined["join"])
        self.assertEqual(source_values, source_values)
        self.assertEqual(order_values, order_values)
        self.assertTrue(any(
            "Excel 원본은 변경하지 않았습니다" in insight
            for insight in result["insights"]
        ))

    def test_excel_analyzer_maps_explicit_different_key_names(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "고객명"),
                    (1, "가"),
                    (2, "나"),
                )),
                FakeWorksheet("주문", (
                    ("주문ID", "구매자ID", "매출"),
                    (101, 1, 100),
                    (102, 1, 300),
                    (103, 2, 200),
                )),
            ],
            join_plan={
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "구매자ID",
                "join_type": "inner",
            },
        )

        joined = result["tables"][-1]
        self.assertEqual("고객ID", joined["join"]["left_key"])
        self.assertEqual("구매자ID", joined["join"]["right_key"])
        self.assertEqual(3, joined["join"]["output_rows"])
        self.assertNotIn("주문/구매자ID", joined["headers"])
        self.assertTrue(any(
            "'고객ID' ↔ '구매자ID' 키" in insight
            for insight in result["insights"]
        ))

    def test_excel_analyzer_aggregates_right_measure_before_join(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "고객명"),
                    (1, "가"),
                    (2, "나"),
                    (3, "다"),
                )),
                FakeWorksheet("주문", (
                    ("주문ID", "구매자ID", "매출"),
                    (101, 1, 100),
                    (102, 1, 300),
                    (103, 2, 200),
                )),
            ],
            join_plan={
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "구매자ID",
                "join_type": "left",
                "right_aggregation": {
                    "column": "매출",
                    "function": "sum",
                },
            },
        )

        joined = result["tables"][-1]
        self.assertEqual(
            ["고객/고객ID", "고객/고객명", "주문/매출 합계"],
            joined["headers"],
        )
        self.assertEqual(
            [[1, "가", 400], [2, "나", 200], [3, "다", None]],
            joined["rows"],
        )
        self.assertEqual(
            {
                "column": "매출",
                "function": "sum",
                "input_rows": 3,
                "groups": 2,
            },
            joined["join"]["right_aggregation"],
        )
        self.assertNotIn("values", joined["join"])

    def test_excel_analyzer_applies_explicit_multi_function_aggregations(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "고객명"),
                    (1, "가"),
                    (2, "나"),
                    (3, "다"),
                )),
                FakeWorksheet("주문", (
                    ("주문ID", "구매자ID", "매출", "수량", "항목"),
                    (101, 1, 100, 2, "A"),
                    (102, 1, 300, 4, "B"),
                    (103, 2, 200, 6, "C"),
                    (104, 2, 400, 8, ""),
                )),
            ],
            join_plan={
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "구매자ID",
                "join_type": "left",
                "right_aggregation": [
                    {"column": "매출", "function": "sum"},
                    {"column": "수량", "function": "average"},
                    {"column": "항목", "function": "count"},
                    {"column": "매출", "function": "minimum"},
                    {"column": "매출", "function": "maximum"},
                ],
            },
        )

        joined = result["tables"][-1]
        self.assertEqual(
            [
                "고객/고객ID",
                "고객/고객명",
                "주문/매출 합계",
                "주문/수량 평균",
                "주문/항목 건수",
                "주문/매출 최솟값",
                "주문/매출 최댓값",
            ],
            joined["headers"],
        )
        self.assertEqual(
            [
                [1, "가", 400, 3, 2, 100, 300],
                [2, "나", 600, 7, 1, 200, 400],
                [3, "다", None, None, None, None, None],
            ],
            joined["rows"],
        )
        self.assertEqual(
            [
                {"column": "매출", "function": "sum", "input_rows": 4, "groups": 2},
                {"column": "수량", "function": "average", "input_rows": 4, "groups": 2},
                {"column": "항목", "function": "count", "input_rows": 3, "groups": 2},
                {"column": "매출", "function": "minimum", "input_rows": 4, "groups": 2},
                {"column": "매출", "function": "maximum", "input_rows": 4, "groups": 2},
            ],
            joined["join"]["right_aggregation"],
        )
        self.assertIn("'매출' 합계", result["insights"][0])
        self.assertIn("'수량' 평균", result["insights"][0])
        self.assertIn("'항목' 건수", result["insights"][0])
        self.assertIn("'매출' 최솟값", result["insights"][0])
        self.assertIn("'매출' 최댓값", result["insights"][0])

    def test_excel_analyzer_preaggregates_duplicate_left_keys(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "매출", "수량", "항목"),
                    (1, 100, 2, "A"),
                    (1, 300, 4, "B"),
                    (2, 200, 6, "C"),
                    (2, 400, 8, ""),
                )),
                FakeWorksheet("주문", (
                    ("구매자ID", "비용"),
                    (1, 50),
                    (2, 70),
                )),
            ],
            join_plan={
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "구매자ID",
                "join_type": "inner",
                "left_aggregation": [
                    {"column": "매출", "function": "sum"},
                    {"column": "수량", "function": "average"},
                    {"column": "항목", "function": "count"},
                    {"column": "매출", "function": "minimum"},
                    {"column": "매출", "function": "maximum"},
                ],
            },
        )

        joined = result["tables"][-1]
        self.assertEqual("one_to_one", joined["join"]["cardinality"])
        self.assertEqual(
            [
                "고객/고객ID",
                "고객/매출 합계",
                "고객/수량 평균",
                "고객/항목 건수",
                "고객/매출 최솟값",
                "고객/매출 최댓값",
                "주문/비용",
            ],
            joined["headers"],
        )
        self.assertEqual(
            [
                [1, 400, 3, 2, 100, 300, 50],
                [2, 600, 7, 1, 200, 400, 70],
            ],
            joined["rows"],
        )
        self.assertEqual(
            [
                {"column": "매출", "function": "sum", "input_rows": 4, "groups": 2},
                {"column": "수량", "function": "average", "input_rows": 4, "groups": 2},
                {"column": "항목", "function": "count", "input_rows": 3, "groups": 2},
                {"column": "매출", "function": "minimum", "input_rows": 4, "groups": 2},
                {"column": "매출", "function": "maximum", "input_rows": 4, "groups": 2},
            ],
            joined["join"]["left_aggregation"],
        )
        self.assertIn("왼쪽 '고객'", result["insights"][0])

    def test_excel_analyzer_blocks_unsafe_aggregate_join_inputs(self):
        cases = (
            (
                [
                    FakeWorksheet("고객", (
                        ("고객ID",),
                        (1,),
                        (1,),
                    )),
                    FakeWorksheet("주문", (
                        ("구매자ID", "매출"),
                        (1, 100),
                    )),
                ],
                "왼쪽 키가 고유",
            ),
            (
                [
                    FakeWorksheet("고객", (("고객ID",), (1,))),
                    FakeWorksheet("주문", (
                        ("구매자ID", "매출"),
                        (1, "미정"),
                    )),
                ],
                "숫자가 아닌 값",
            ),
        )
        plan = {
            "left_sheet": "고객",
            "right_sheet": "주문",
            "left_key": "고객ID",
            "right_key": "구매자ID",
            "join_type": "left",
            "right_aggregation": {"column": "매출", "function": "sum"},
        }

        for worksheets, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    WorkflowJoinValidationError, message
                ):
                    self._analyze_sheets(worksheets, join_plan=plan)

        normalized_duplicate_plan = dict(plan)
        normalized_duplicate_plan["right_aggregation"] = [
            {"column": "매 출", "function": "sum"},
            {"column": "매출", "function": "sum"},
        ]
        with self.assertRaisesRegex(
            WorkflowJoinValidationError, "중복 지정"
        ):
            self._analyze_sheets(
                [
                    FakeWorksheet("고객", (("고객ID",), (1,))),
                    FakeWorksheet(
                        "주문", (("구매자ID", "매출"), (1, 100))
                    ),
                ],
                join_plan=normalized_duplicate_plan,
            )

        minimum_plan = dict(plan)
        minimum_plan["right_aggregation"] = {
            "column": "매출",
            "function": "minimum",
        }
        with self.assertRaisesRegex(
            WorkflowJoinValidationError, "숫자가 아닌 값"
        ):
            self._analyze_sheets(
                [
                    FakeWorksheet("고객", (("고객ID",), (1,))),
                    FakeWorksheet(
                        "주문", (("구매자ID", "매출"), (1, "미정"))
                    ),
                ],
                join_plan=minimum_plan,
            )

    def test_excel_analyzer_left_join_keeps_unmatched_left_rows(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "고객명"),
                    (1, "가"),
                    (2, "나"),
                    (3, "다"),
                )),
                FakeWorksheet("주문", (
                    ("주문ID", "고객ID"),
                    (101, 1),
                    (102, 1),
                    (103, 2),
                )),
            ],
            join_plan=self._join_plan("left"),
        )

        joined = result["tables"][-1]
        self.assertEqual(4, joined["join"]["output_rows"])
        self.assertEqual(1, joined["join"]["unmatched_left_rows"])
        self.assertEqual([3, "다", None], joined["rows"][-1])

    def test_excel_analyzer_preserves_leading_zero_text_identifier(self):
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (
                    ("고객ID", "고객명"),
                    ("001", "문자 코드"),
                    (1, "숫자 코드"),
                )),
                FakeWorksheet("주문", (
                    ("주문ID", "고객ID"),
                    (101, 1),
                )),
            ],
            join_plan=self._join_plan(),
        )

        joined = result["tables"][-1]
        self.assertEqual(1, joined["total_rows"])
        self.assertEqual([1, "숫자 코드", 101], joined["rows"][0])

    def test_excel_analyzer_blocks_missing_duplicate_or_measure_join_key(self):
        cases = (
            (
                [
                    FakeWorksheet("고객", (("고객ID",), (1,))),
                    FakeWorksheet("주문", (("주문ID",), (101,))),
                ],
                self._join_plan(),
                "열을 정확히 하나",
            ),
            (
                [
                    FakeWorksheet(
                        "고객", (("고객ID", "고객-ID"), (1, 1))
                    ),
                    FakeWorksheet("주문", (("고객ID",), (1,))),
                ],
                self._join_plan(),
                "열을 정확히 하나",
            ),
            (
                [
                    FakeWorksheet("고객", (("매출",), (100,))),
                    FakeWorksheet("주문", (("매출",), (100,))),
                ],
                {
                    "left_sheet": "고객",
                    "right_sheet": "주문",
                    "left_key": "매출",
                    "right_key": "매출",
                    "join_type": "inner",
                },
                "측정값 열",
            ),
        )

        for worksheets, plan, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    WorkflowJoinValidationError, message
                ):
                    self._analyze_sheets(
                        worksheets,
                        join_plan=plan,
                    )

    def test_excel_analyzer_blocks_explicit_many_to_many_join(self):
        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "다대다 관계",
        ) as raised:
            self._analyze_sheets(
                [
                    FakeWorksheet("고객", (
                        ("고객ID", "고객명"),
                        (1, "가"),
                        (1, "가2"),
                    )),
                    FakeWorksheet("주문", (
                        ("주문ID", "고객ID"),
                        (101, 1),
                        (102, 1),
                    )),
                ],
                join_plan=self._join_plan(),
            )

        self.assertEqual("validation_error", raised.exception.error_type)
        self.assertEqual("blocked", raised.exception.status)
        self.assertFalse(raised.exception.retryable)

    def test_excel_analyzer_caps_join_preview_and_total_preview_rows(self):
        orders = tuple(
            [("주문ID", "고객ID")]
            + [(index, 1) for index in range(1, 102)]
        )
        result = self._analyze_sheets(
            [
                FakeWorksheet("고객", (("고객ID", "고객명"), (1, "가"))),
                FakeWorksheet("주문", orders),
            ],
            join_plan=self._join_plan(),
        )

        joined = result["tables"][-1]
        self.assertEqual(101, joined["total_rows"])
        self.assertEqual(100, joined["included_rows"])
        self.assertTrue(joined["join"]["truncated"])
        self.assertLessEqual(
            sum(table["included_rows"] for table in result["tables"]),
            500,
        )

    def test_excel_analyzer_reads_only_explicit_source_scope(self):
        selected_sheet = FakeWorksheet("매출", (
            ("지역", "담당자", "매출"),
            ("서울", "김", 100),
            ("부산", "이", 200),
            ("대전", "박", 300),
        ))
        excluded_sheet = FakeWorksheet("비용", (
            ("항목", "비용"),
            ("인건비", 50),
        ))
        source_scope = {
            "kind": "range",
            "sheet_name": "매출",
            "address": "B1:C3",
        }

        result = self._analyze_sheets(
            [selected_sheet, excluded_sheet],
            source_scope=source_scope,
        )

        self.assertEqual(1, len(result["tables"]))
        table = result["tables"][0]
        self.assertEqual("매출!B1:C3", table["name"])
        self.assertEqual(["담당자", "매출"], table["headers"])
        self.assertEqual([["김", 100], ["이", 200]], table["rows"])
        self.assertEqual(source_scope, table["source_scope"])
        self.assertEqual([], result["charts"])
        self.assertEqual(0, selected_sheet.UsedRange.value_reads)
        self.assertEqual(0, excluded_sheet.UsedRange.value_reads)

    def test_excel_analyzer_blocks_missing_or_hidden_source_scope_sheet(self):
        for source_scope in (
            {"kind": "range", "sheet_name": "없음", "address": "A1:B2"},
            {"kind": "range", "sheet_name": "숨김", "address": "A1:B2"},
        ):
            with self.subTest(source_scope=source_scope):
                with self.assertRaisesRegex(
                    WorkflowSourceScopeValidationError,
                    "표시된 Excel 시트",
                ):
                    self._analyze_sheets(
                        [
                            FakeWorksheet("매출", (("A", "B"), (1, 2))),
                            FakeWorksheet(
                                "숨김", (("A", "B"), (1, 2)), visible=0
                            ),
                        ],
                        source_scope=source_scope,
                    )

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

    def test_excel_analyzer_detects_bounded_relationship_and_pivot_summary(self):
        result = self._analyze_sheets([
            FakeWorksheet("고객", (
                ("고객ID", "고객명"),
                (1, "가"),
                (2, "나"),
                (3, "다"),
            )),
            FakeWorksheet("주문", (
                ("주문ID", "고객ID", "지역", "매출"),
                (101, "1", "서울", 100),
                (102, "1", "서울", 300),
                (103, "2", "부산", 200),
                (104, "4", "대전", 150),
                (105, "5", "대구", 120),
                (106, "6", "광주", 110),
                (107, "7", "인천", 90),
            )),
        ])

        relationship = result["tables"][0]["relationships"][0]
        self.assertEqual("주문", relationship["other_sheet"])
        self.assertEqual("고객ID", relationship["column"])
        self.assertEqual("one_to_many", relationship["cardinality"])
        self.assertEqual(2, relationship["matched_key_count"])
        self.assertNotIn("values", relationship)
        pivot = result["tables"][1]["pivot_summaries"][0]
        self.assertEqual("지역", pivot["group_by"])
        self.assertEqual("매출", pivot["value_column"])
        self.assertEqual(6, pivot["group_count"])
        self.assertEqual(5, len(pivot["groups"]))
        self.assertTrue(pivot["truncated"])
        self.assertEqual(
            {"label": "서울", "count": 2, "sum": 400.0},
            pivot["groups"][0],
        )
        self.assertTrue(any(
            "시트 관계 후보" in insight for insight in result["insights"]
        ))
        self.assertTrue(any(
            "'지역'별 '매출' 합계" in insight
            for insight in result["insights"]
        ))

    def test_excel_relationship_inspection_returns_only_schema_candidates(self):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("고객", (
                ("고객ID", "고객명"),
                (1, "가"),
                (2, "나"),
                (3, "다"),
            )),
            FakeWorksheet("주문", (
                ("고객 ID", "주문액"),
                (1, 100),
                (2, 200),
                (3, 300),
            )),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        result = analyzer.relationship_candidates({
            "source_path": str(self.source),
        })

        self.assertTrue(lease.cleaned)
        self.assertEqual("candidate_found", result["status"])
        self.assertEqual(1, result["candidate_count"])
        self.assertEqual(
            {
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "고객 ID",
                "match_basis": "normalized_header",
                "ambiguous": False,
                "cardinality": "one_to_one",
                "matched_key_count": 3,
                "left_distinct_count": 3,
                "right_distinct_count": 3,
                "left_coverage": 1.0,
                "right_coverage": 1.0,
                "sample_limited": False,
                "requires_preaggregation": False,
                "confidence": "high",
            },
            result["candidates"][0],
        )
        self.assertFalse(result["automatic_execution_allowed"])
        self.assertFalse(result["raw_cell_values_stored"])
        self.assertFalse(result["document_paths_reported"])
        self.assertNotIn("rows", result)
        self.assertNotIn("source_path", result)

    def test_excel_relationship_inspection_suggests_different_named_keys_for_review(self):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("고객", (
                ("고객ID", "고객명"),
                (1, "가"),
                (2, "나"),
                (3, "다"),
                (4, "라"),
            )),
            FakeWorksheet("주문", (
                ("구매자ID", "주문액"),
                (1, 100),
                (2, 200),
                (3, 300),
                (3, 150),
                (4, 400),
            )),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        result = analyzer.relationship_candidates({
            "source_path": str(self.source),
        })

        self.assertTrue(lease.cleaned)
        self.assertEqual(1, result["candidate_count"])
        candidate = result["candidates"][0]
        self.assertEqual("고객ID", candidate["left_key"])
        self.assertEqual("구매자ID", candidate["right_key"])
        self.assertEqual("value_overlap", candidate["match_basis"])
        self.assertFalse(candidate["ambiguous"])
        self.assertEqual("one_to_many", candidate["cardinality"])
        self.assertEqual("review_required", candidate["confidence"])
        self.assertFalse(result["automatic_execution_allowed"])

    def test_excel_relationship_inspection_rejects_weak_different_named_keys(self):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("고객", (
                ("고객ID",), (1,), (2,), (3,), (4,),
            )),
            FakeWorksheet("주문", (
                ("구매자ID",), (1,), (2,), (8,), (9,),
            )),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        result = analyzer.relationship_candidates({
            "source_path": str(self.source),
        })

        self.assertTrue(lease.cleaned)
        self.assertEqual("no_candidate", result["status"])
        self.assertEqual([], result["candidates"])

    def test_excel_relationship_inspection_marks_competing_key_matches_ambiguous(self):
        lease = FakeLease()
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("고객", (
                ("고객ID",), (1,), (2,), (3,), (4,),
            )),
            FakeWorksheet("주문", (
                ("고객ID", "구매자ID"),
                (1, 1),
                (2, 2),
                (3, 3),
                (4, 4),
            )),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        result = analyzer.relationship_candidates({
            "source_path": str(self.source),
        })

        self.assertTrue(lease.cleaned)
        self.assertEqual(2, result["candidate_count"])
        self.assertEqual(
            ["normalized_header", "value_overlap"],
            [candidate["match_basis"] for candidate in result["candidates"]],
        )
        self.assertEqual(
            [False, True],
            [candidate["ambiguous"] for candidate in result["candidates"]],
        )
        self.assertEqual(
            ["high", "review_required"],
            [candidate["confidence"] for candidate in result["candidates"]],
        )

    def test_excel_relationship_inspection_caps_candidates_and_prefers_same_names(self):
        lease = FakeLease()
        headers = tuple(f"키{index}ID" for index in range(12))
        rows = tuple(tuple([value] * 12) for value in range(1, 5))
        workbook = FakeWorkbook(self.source, [
            FakeWorksheet("왼쪽", (headers, *rows)),
            FakeWorksheet("오른쪽", (headers, *rows)),
        ])
        analyzer = ExcelSalesAnalyzer(com_runtime=FakeComRuntime())
        analyzer._open = lambda source_path: (lease, workbook)

        result = analyzer.relationship_candidates({
            "source_path": str(self.source),
        })

        self.assertTrue(lease.cleaned)
        self.assertEqual(10, result["candidate_count"])
        self.assertEqual(10, len(result["candidates"]))
        self.assertTrue(all(
            candidate["match_basis"] == "normalized_header"
            for candidate in result["candidates"]
        ))

    def test_excel_analyzer_never_treats_shared_measure_as_join_key(self):
        result = self._analyze_sheets([
            FakeWorksheet("매출", (
                ("항목", "금액"), ("A", 100), ("B", 200),
            )),
            FakeWorksheet("비용", (
                ("항목", "금액"), ("C", 100), ("D", 200),
            )),
        ])

        relationships = [
            relationship
            for table in result["tables"]
            for relationship in table.get("relationships") or []
        ]
        self.assertFalse(any(
            relationship["column"] == "금액"
            for relationship in relationships
        ))

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

    def test_prepare_declares_content_free_step_execution_contracts(self):
        state = self.executor.prepare(self.source, report_format="both")
        contracts = state["step_contracts"]

        self.assertEqual(1, state["step_contract_schema_version"])
        self.assertEqual(state["step_order"], list(contracts))
        self.assertEqual([], contracts["analyze_excel"]["depends_on"])
        self.assertEqual(
            ["analyze_excel"],
            contracts["create_word_report"]["depends_on"],
        )
        self.assertEqual(
            ["create_word_report"],
            contracts["create_hwp_report"]["depends_on"],
        )
        self.assertEqual(
            ["create_hwp_report"],
            contracts["create_powerpoint_summary"]["depends_on"],
        )
        self.assertEqual(
            "read_only_analysis", contracts["analyze_excel"]["effect"]
        )
        self.assertTrue(
            all(
                contract["effect"] == "create_owned_file"
                for name, contract in contracts.items()
                if name != "analyze_excel"
            )
        )
        idempotency_keys = {
            contract["idempotency_key"] for contract in contracts.values()
        }
        self.assertEqual(len(contracts), len(idempotency_keys))
        self.assertTrue(
            all(
                re.fullmatch(r"[A-F0-9]{64}", key)
                for key in idempotency_keys
            )
        )
        serialized = str(contracts)
        self.assertNotIn(str(self.source), serialized)
        self.assertNotIn("owned-excel-fixture", serialized)

    def test_hwp_plans_block_before_approval_when_environment_is_missing(self):
        for report_format in ("hwp", "both"):
            with self.subTest(report_format=report_format):
                analyzer = FakeAnalyzer()
                hwp = PreflightWriter("hwp", fail_on_call=1)
                store = self.root / f"{report_format}-preflight-state"
                executor = WorkflowExecutor(
                    store,
                    analyzer=analyzer,
                    word_writer=FakeWriter("word"),
                    hwp_writer=hwp,
                    powerpoint_writer=FakeWriter("ppt", slides=5),
                )

                with self.assertRaisesRegex(
                    HwpSecurityModuleUnavailable,
                    "보안 모듈 준비",
                ) as raised:
                    executor.prepare(self.source, report_format=report_format)

                self.assertEqual(
                    "environment_error", raised.exception.error_type
                )
                self.assertEqual(1, hwp.preflight_calls)
                self.assertEqual(0, analyzer.calls)
                self.assertFalse(store.exists())

    def test_word_prepare_does_not_require_hwp_environment(self):
        hwp = PreflightWriter("hwp", fail_on_call=1)
        executor = WorkflowExecutor(
            self.root / "word-preflight-state",
            analyzer=FakeAnalyzer(),
            word_writer=FakeWriter("word"),
            hwp_writer=hwp,
            powerpoint_writer=FakeWriter("ppt", slides=5),
        )

        state = executor.prepare(self.source, report_format="word")

        self.assertEqual("approval_required", state["status"])
        self.assertEqual(0, hwp.preflight_calls)

    def test_hwp_approval_rechecks_environment_before_analysis_or_state_save(self):
        analyzer = FakeAnalyzer()
        hwp = PreflightWriter("hwp", fail_on_call=2)
        executor = WorkflowExecutor(
            self.root / "hwp-recheck-state",
            analyzer=analyzer,
            word_writer=FakeWriter("word"),
            hwp_writer=hwp,
            powerpoint_writer=FakeWriter("ppt", slides=5),
        )
        state = executor.prepare(self.source, report_format="hwp")

        with self.assertRaisesRegex(
            HwpSecurityModuleUnavailable,
            "보안 모듈 준비",
        ):
            executor.start(state)

        self.assertEqual(2, hwp.preflight_calls)
        self.assertEqual(0, analyzer.calls)
        self.assertFalse(executor._path(state["workflow_id"]).exists())

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

    def test_workflow_records_relationship_and_pivot_verification_counts(self):
        class EnrichedAnalyzer(FakeAnalyzer):
            def run(self, context):
                value = super().run(context)
                value["tables"][0]["relationships"] = [{
                    "other_sheet": "주문",
                    "column": "고객ID",
                    "cardinality": "one_to_many",
                    "matched_key_count": 2,
                }]
                value["tables"][0]["pivot_summaries"] = [{
                    "group_by": "지역",
                    "value_column": "매출",
                    "group_count": 2,
                    "groups": [],
                }]
                return value

        executor = WorkflowExecutor(
            self.root / "enriched-state",
            analyzer=EnrichedAnalyzer(),
            word_writer=FakeWriter("word"),
            hwp_writer=FakeWriter("hwp"),
            powerpoint_writer=FakeWriter("ppt", slides=5),
        )

        result = executor.start(executor.prepare(self.source))
        verification = result["verification_results"]["analyze_excel"]

        self.assertEqual(1, verification["relationship_count"])
        self.assertEqual(1, verification["pivot_summary_count"])

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

    def test_default_hwp_writer_times_out_and_cleans_only_reported_process(self):
        output = self.root / "시간초과_보고서.hwp"
        stop_calls = []

        class FakeProcess:
            def __init__(self, args):
                self.args = args
                self.alive = True
                self.terminated = False

            def start(self):
                Path(self.args[0]["output_path"]).write_bytes(b"partial")
                self.args[5].put_nowait(222)

            def join(self, _timeout=None):
                return None

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True
                self.alive = False

            def kill(self):
                self.alive = False

        class FakeProcessContext:
            def Queue(self, maxsize=0):
                return queue.Queue(maxsize=maxsize)

            def Process(self, target, args):
                self.target = target
                self.process = FakeProcess(args)
                return self.process

        process_context = FakeProcessContext()
        writer = HwpReportWriter(
            timeout_seconds=5,
            process_context_factory=lambda: process_context,
            process_ids=lambda: {111},
            process_stopper=lambda process_id, baseline: stop_calls.append(
                (process_id, set(baseline))
            ),
            security_module_resolver=lambda: "TestSecurityModule",
        )

        with self.assertRaisesRegex(
            HwpWorkflowTimeout,
            "파일 접근 확인 창 또는 보안 모듈",
        ):
            writer.run(
                {"output_path": str(output), "preferences": {}},
                product(self.source),
            )

        self.assertTrue(process_context.process.terminated)
        self.assertEqual([(222, {111})], stop_calls)
        self.assertFalse(output.exists())

    def test_default_hwp_writer_blocks_before_process_when_module_is_missing(self):
        process_context_called = False

        def process_context_factory():
            nonlocal process_context_called
            process_context_called = True
            raise AssertionError("missing module must block before process")

        writer = HwpReportWriter(
            process_context_factory=process_context_factory,
            security_module_resolver=lambda: None,
        )

        with self.assertRaisesRegex(
            HwpSecurityModuleUnavailable,
            "설치·등록",
        ) as raised:
            writer.run(
                {
                    "output_path": str(self.root / "보안모듈없음.hwp"),
                    "preferences": {},
                },
                product(self.source),
            )

        self.assertEqual("environment_error", raised.exception.error_type)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(
            "https://developer.hancom.com/hwpautomation",
            raised.exception.diagnostic_context["setup_guide_url"],
        )
        self.assertEqual(
            r"HKCU\Software\HNC\HwpAutomation\Modules",
            raised.exception.diagnostic_context["registry_location"],
        )
        self.assertFalse(
            raised.exception.diagnostic_context["automatic_install_attempted"]
        )
        self.assertFalse(process_context_called)

    def test_hwp_environment_diagnostics_survive_workflow_failure_wrapper(self):
        class MissingModuleWriter:
            def run(self, context, work_product):
                raise HwpSecurityModuleUnavailable(
                    "한글 공식 보안 모듈 준비가 필요합니다."
                )

        executor = WorkflowExecutor(
            self.root / "hwp-diagnostic-state",
            analyzer=FakeAnalyzer(),
            word_writer=FakeWriter("word"),
            hwp_writer=MissingModuleWriter(),
            powerpoint_writer=FakeWriter("ppt", slides=5),
        )
        state = executor.prepare(self.source, report_format="hwp")

        with self.assertRaises(WorkflowExecutionError) as raised:
            executor.start(state)

        diagnostic = raised.exception.diagnostic_context
        self.assertEqual(
            "hwp_automation_security_module",
            diagnostic["environment_component"],
        )
        self.assertEqual(
            "https://developer.hancom.com/hwpautomation",
            diagnostic["setup_guide_url"],
        )
        self.assertEqual(
            "environment_error", diagnostic["cause_error_type"]
        )
        self.assertFalse(diagnostic["automatic_install_attempted"])

    def test_hwp_worker_security_activation_failure_keeps_environment_type(self):
        class FakeProcess:
            def __init__(self, args):
                self.args = args

            def start(self):
                self.args[4].put_nowait({
                    "success": False,
                    "error_type": "environment_error",
                    "message": "등록된 보안 모듈을 활성화하지 못했습니다.",
                })

            def join(self, _timeout=None):
                return None

            def is_alive(self):
                return False

        class FakeProcessContext:
            def Queue(self, maxsize=0):
                return queue.Queue(maxsize=maxsize)

            def Process(self, target, args):
                return FakeProcess(args)

        writer = HwpReportWriter(
            process_context_factory=FakeProcessContext,
            process_ids=lambda: set(),
            security_module_resolver=lambda: "RegisteredButRejected",
        )

        with self.assertRaisesRegex(
            HwpSecurityModuleUnavailable,
            "활성화하지 못했습니다",
        ) as raised:
            writer.run(
                {
                    "output_path": str(self.root / "활성화실패.hwp"),
                    "preferences": {},
                },
                product(self.source),
            )

        self.assertEqual("environment_error", raised.exception.error_type)
        self.assertEqual("blocked", raised.exception.status)
        self.assertTrue(raised.exception.retryable)

    def test_hwp_timeout_keeps_resume_state_and_timeout_diagnosis(self):
        class TimeoutWriter:
            def run(self, context, work_product):
                raise HwpWorkflowTimeout("simulated HWP save timeout")

        executor = WorkflowExecutor(
            self.root / "hwp-timeout-state",
            analyzer=FakeAnalyzer(),
            word_writer=FakeWriter("word"),
            hwp_writer=TimeoutWriter(),
            powerpoint_writer=FakeWriter("ppt", slides=5),
        )
        state = executor.prepare(self.source, report_format="hwp")

        with self.assertRaises(WorkflowExecutionError) as raised:
            executor.start(state)

        self.assertEqual("timeout", raised.exception.error_type)
        self.assertTrue(raised.exception.retryable)
        stored = executor.load(state["workflow_id"])
        self.assertEqual("failed", stored["status"])
        self.assertEqual("create_hwp_report", stored["failed_step"])
        self.assertEqual(["analyze_excel"], stored["successful_steps"])

    def test_retryable_blocked_workflow_is_resumable_but_validation_block_is_not(self):
        retryable = self.executor.prepare(self.source)
        retryable["status"] = "blocked"
        retryable["failure"] = {
            "error_type": "environment_error",
            "status": "blocked",
            "retryable": True,
        }
        self.executor._save(retryable)

        self.assertEqual(
            retryable["workflow_id"],
            self.executor.latest_for_source(self.source)["workflow_id"],
        )

        nonretryable = self.executor.prepare(self.source)
        nonretryable["status"] = "blocked"
        nonretryable["failure"] = {
            "error_type": "validation_error",
            "status": "blocked",
            "retryable": False,
        }
        self.executor._save(nonretryable)

        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "새 요청",
        ):
            self.executor.run(nonretryable["workflow_id"])

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

    def test_workflow_intent_requires_complete_numbered_candidate_contract(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {"app_type": "excel"}

        valid = analyzer.analyze(
            "조인 키 후보 1번으로 왼쪽 조인해서 Word 보고서와 5장 PPT 만들어줘",
            context,
        )
        missing_join_type = analyzer.analyze(
            "2번 후보로 조인해서 보고서와 PPT 만들어줘",
            context,
        )
        invalid_number = analyzer.analyze(
            "11번 후보로 내부 조인해서 보고서와 PPT 만들어줘",
            context,
        )
        ignored_aggregation = analyzer.analyze(
            "1번 후보로 매출을 합계 집계해서 왼쪽 조인하고 "
            "보고서와 PPT 만들어줘",
            context,
        )

        self.assertEqual("create_business_workflow", valid.operation)
        self.assertEqual(1, valid.params["relationship_candidate_index"])
        self.assertEqual(
            "left", valid.params["relationship_candidate_join_type"]
        )
        self.assertTrue(valid.params["join_requested"])
        self.assertIsNone(valid.params["join_plan"])
        self.assertIn("후보 1번", valid.description)
        self.assertIn(
            "내부 또는 왼쪽",
            missing_join_type.params["relationship_candidate_error"],
        )
        self.assertIn(
            "1~10번",
            invalid_number.params["relationship_candidate_error"],
        )
        self.assertIn(
            "집계 조건을 생략해 실행하지 않습니다",
            ignored_aggregation.params["relationship_candidate_error"],
        )

    def test_relationship_candidate_cache_is_session_scoped_and_expires(self):
        from engine.workflows.business_workflow import file_fingerprint

        fingerprint = file_fingerprint(self.source)
        candidate = {
            "left_sheet": "고객",
            "right_sheet": "주문",
            "left_key": "고객ID",
            "right_key": "구매자ID",
            "match_basis": "value_overlap",
            "ambiguous": False,
            "cardinality": "one_to_many",
            "matched_key_count": 4,
            "left_distinct_count": 4,
            "right_distinct_count": 5,
            "left_coverage": 1.0,
            "right_coverage": 0.8,
            "sample_limited": False,
            "requires_preaggregation": False,
            "confidence": "review_required",
        }
        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "후보 형식",
        ):
            self.executor.remember_relationship_candidates(
                source_path=self.source,
                source_fingerprint=fingerprint,
                edit_session_id="session-a",
                candidates=[{**candidate, "raw_values": ["비공개"]}],
            )
        self.executor.remember_relationship_candidates(
            source_path=self.source,
            source_fingerprint=fingerprint,
            edit_session_id="session-a",
            candidates=[candidate],
        )

        resolved = self.executor.resolve_relationship_candidate(
            source_path=self.source,
            source_fingerprint=fingerprint,
            edit_session_id="session-a",
            candidate_index=1,
        )

        self.assertEqual(candidate, resolved)
        self.assertNotIn(
            "source_path", self.executor._relationship_candidate_cache
        )
        self.assertIn(
            "source_identity_hash", self.executor._relationship_candidate_cache
        )
        resolved["left_sheet"] = "변조"
        self.assertEqual(
            "고객",
            self.executor._relationship_candidate_cache["candidates"][0][
                "left_sheet"
            ],
        )
        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "파일·편집 세션",
        ):
            self.executor.resolve_relationship_candidate(
                source_path=self.source,
                source_fingerprint=fingerprint,
                edit_session_id="session-b",
                candidate_index=1,
            )
        self.executor.remember_relationship_candidates(
            source_path=self.source,
            source_fingerprint=fingerprint,
            edit_session_id="session-a",
            candidates=[candidate],
        )
        self.executor._relationship_candidate_cache["captured_at"] -= 601
        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "시간이 지났거나",
        ):
            self.executor.resolve_relationship_candidate(
                source_path=self.source,
                source_fingerprint=fingerprint,
                edit_session_id="session-a",
                candidate_index=1,
            )

    def test_workflow_intent_requires_complete_explicit_join_contract(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {"app_type": "excel"}

        inspection = analyzer.analyze("조인 키 후보 알려줘", context)
        joined = analyzer.analyze(
            "고객 시트와 주문 시트를 고객ID로 내부 조인해서 "
            "Word 보고서와 5장짜리 PPT 만들어줘",
            context,
        )
        quoted = analyzer.analyze(
            '"고객 목록" 시트의 "고객 ID"와 "주문 내역" 시트의 '
            '"구매자 ID"로 '
            "왼쪽 조인해서 보고서와 PPT 만들어줘",
            context,
        )
        mapped = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 내부 조인해서 "
            "Word 보고서와 5장짜리 PPT 만들어줘",
            context,
        )
        ambiguous = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트를 구매자ID로 내부 조인해서 "
            "보고서와 PPT 만들어줘",
            context,
        )
        aggregated = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 주문 시트의 "
            "매출을 합계 집계해서 왼쪽 조인해서 Word 보고서와 "
            "5장짜리 PPT 만들어줘",
            context,
        )
        multi_aggregated = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 주문 시트의 "
            "매출을 합계 집계하고 주문 시트의 수량을 평균 집계하고 "
            "주문 시트의 주문ID를 건수 집계하고 주문 시트의 매출을 "
            "최솟값 집계하고 주문 시트의 매출을 최댓값 집계해서 왼쪽 "
            "조인해서 Word "
            "보고서와 5장짜리 PPT 만들어줘",
            context,
        )
        minmax_variants = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 주문 시트의 "
            "매출을 최소값 집계하고 주문 시트의 매출을 최대값 집계해서 "
            "왼쪽 조인해서 Word 보고서와 PPT 만들어줘",
            context,
        )
        left_aggregated = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 고객 시트의 "
            "매출을 합계 집계해서 왼쪽 조인해서 보고서와 PPT 만들어줘",
            context,
        )
        unknown_aggregation_side = analyzer.analyze(
            "고객 시트의 고객ID와 주문 시트의 구매자ID로 재고 시트의 "
            "매출을 합계 집계해서 왼쪽 조인해서 보고서와 PPT 만들어줘",
            context,
        )
        incomplete = analyzer.analyze(
            "고객 시트와 주문 시트를 고객ID로 조인해서 "
            "보고서와 PPT 만들어줘",
            context,
        )

        self.assertEqual("inspect_excel_relationships", inspection.operation)
        self.assertEqual(
            {
                "left_sheet": "고객",
                "right_sheet": "주문",
                "left_key": "고객ID",
                "right_key": "고객ID",
                "join_type": "inner",
            },
            joined.params["join_plan"],
        )
        self.assertEqual("left", quoted.params["join_plan"]["join_type"])
        self.assertEqual("고객 목록", quoted.params["join_plan"]["left_sheet"])
        self.assertEqual("고객 ID", quoted.params["join_plan"]["left_key"])
        self.assertEqual("구매자 ID", quoted.params["join_plan"]["right_key"])
        self.assertEqual("고객ID", mapped.params["join_plan"]["left_key"])
        self.assertEqual("구매자ID", mapped.params["join_plan"]["right_key"])
        self.assertIn("고객ID ↔ 구매자ID 키 매핑", mapped.description)
        self.assertTrue(ambiguous.params["join_requested"])
        self.assertIsNone(ambiguous.params["join_plan"])
        self.assertIn("양쪽 키를 모두", ambiguous.params["join_error"])
        self.assertEqual(
            {"column": "매출", "function": "sum"},
            aggregated.params["join_plan"]["right_aggregation"],
        )
        self.assertIn("주문/매출 합계 집계", aggregated.description)
        self.assertEqual(
            [
                {"column": "매출", "function": "sum"},
                {"column": "수량", "function": "average"},
                {"column": "주문ID", "function": "count"},
                {"column": "매출", "function": "minimum"},
                {"column": "매출", "function": "maximum"},
            ],
            multi_aggregated.params["join_plan"]["right_aggregation"],
        )
        self.assertIn("주문/매출 합계", multi_aggregated.description)
        self.assertIn("주문/수량 평균", multi_aggregated.description)
        self.assertIn("주문/주문ID 건수", multi_aggregated.description)
        self.assertIn("주문/매출 최솟값", multi_aggregated.description)
        self.assertIn("주문/매출 최댓값", multi_aggregated.description)
        self.assertEqual(
            [
                {"column": "매출", "function": "minimum"},
                {"column": "매출", "function": "maximum"},
            ],
            minmax_variants.params["join_plan"]["right_aggregation"],
        )
        self.assertEqual(
            {"column": "매출", "function": "sum"},
            left_aggregated.params["join_plan"]["left_aggregation"],
        )
        self.assertIn("고객/매출 합계", left_aggregated.description)
        self.assertIsNone(unknown_aggregation_side.params["join_plan"])
        self.assertIn(
            "왼쪽 또는 오른쪽 시트",
            unknown_aggregation_side.params["join_error"],
        )
        self.assertTrue(incomplete.params["join_requested"])
        self.assertIsNone(incomplete.params["join_plan"])
        self.assertIn("조인 방식", incomplete.params["join_error"])

    def test_workflow_intent_requires_explicit_valid_selection_scope(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {
            "app_type": "excel",
            "selection_kind": "range",
            "active_container": "7월 실적",
            "selection_reference": "$B$2:$F$18",
        }

        scoped = analyzer.analyze(
            "선택한 범위만 분석해서 Word 보고서와 5장짜리 PPT 만들어줘",
            context,
        )
        whole = analyzer.analyze(
            "이 엑셀을 분석해서 Word 보고서와 5장짜리 PPT 만들어줘",
            context,
        )
        missing = analyzer.analyze(
            "선택한 범위만 분석해서 Word 보고서와 PPT 만들어줘",
            {**context, "selection_kind": "cell", "selection_reference": "B2"},
        )

        self.assertEqual(
            {
                "kind": "range",
                "sheet_name": "7월 실적",
                "address": "$B$2:$F$18",
            },
            scoped.params["source_scope"],
        )
        self.assertIn("현재 선택 Excel 범위만", scoped.description)
        self.assertIsNone(whole.params.get("source_scope"))
        self.assertFalse(whole.params.get("source_scope_requested"))
        self.assertTrue(missing.params["source_scope_requested"])
        self.assertIsNone(missing.params["source_scope"])
        self.assertIn("연속 셀 범위", missing.params["source_scope_error"])

    def test_workflow_intent_recognizes_approved_reuse_lifecycle(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {"app_type": "excel"}

        remember = analyzer.analyze("이 워크플로 기억해", context)
        reuse = analyzer.analyze("지난번처럼 해줘", context)
        overridden = analyzer.analyze(
            "지난번처럼 한글 보고서와 7장짜리 PPT로 해줘",
            context,
        )
        forget = analyzer.analyze("워크플로 기억 취소", context)

        self.assertEqual(
            "activate_business_workflow_skill", remember.operation
        )
        self.assertTrue(reuse.params["reuse_approved_skill"])
        self.assertIsNone(reuse.params["report_format"])
        self.assertEqual("hwp", overridden.params["report_format"])
        self.assertEqual(7, overridden.params["slide_count"])
        self.assertEqual(
            "deactivate_business_workflow_skill", forget.operation
        )

    def test_current_excel_reference_can_supply_omitted_analysis_phrase(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {"app_type": "excel"}

        generic = analyzer.analyze(
            "이거 보고서랑 발표자료 만들어줘",
            context,
        )
        explicit = analyzer.analyze(
            "현재 엑셀을 한글 보고서와 발표자료 7장으로 정리해줘",
            context,
        )

        self.assertEqual("create_business_workflow", generic.operation)
        self.assertTrue(generic.params["contextual_current_document"])
        self.assertEqual("word", generic.params["report_format"])
        self.assertIn("현재 연결 Excel 전체", generic.description)
        self.assertEqual("hwp", explicit.params["report_format"])
        self.assertEqual(7, explicit.params["slide_count"])
        self.assertIsNone(
            analyzer.analyze(
                "이거 보고서랑 발표자료가 뭐야?",
                context,
            )
        )
        self.assertIsNone(
            analyzer.analyze(
                "이거 보고서랑 발표자료 만들어줘",
                {"app_type": "word"},
            )
        )

    def test_recent_verified_artifact_open_intent_is_strict_and_typed(self):
        analyzer = StructuredWorkflowIntentAnalyzer()
        context = {"app_type": "excel"}

        generic_report = analyzer.analyze(
            "방금 만든 보고서 열어줘",
            context,
        )
        word_report = analyzer.analyze(
            "방금 만든 워드 보고서 앞으로 보여줘",
            context,
        )
        hwp_report = analyzer.analyze(
            "아까 만든 한글 보고서 포커스해줘",
            context,
        )
        presentation = analyzer.analyze(
            "최근 만든 발표자료 열어줘",
            context,
        )
        edit_handoff = analyzer.analyze(
            "방금 만든 보고서를 편집 문서로 연결해줘",
            context,
        )

        self.assertEqual("open_recent_workflow_artifact", generic_report.operation)
        self.assertEqual("report", generic_report.params["artifact_kind"])
        self.assertEqual("word_report", word_report.params["artifact_kind"])
        self.assertEqual("hwp_report", hwp_report.params["artifact_kind"])
        self.assertEqual("presentation", presentation.params["artifact_kind"])
        self.assertEqual(
            "connect_recent_workflow_artifact",
            edit_handoff.operation,
        )
        self.assertEqual("report", edit_handoff.params["artifact_kind"])
        self.assertIsNone(
            analyzer.analyze("방금 만든 보고서가 뭐야?", context)
        )
        self.assertIsNone(
            analyzer.analyze(
                "방금 만든 보고서 열어줘",
                {"app_type": "word"},
            )
        )
        self.assertIsNone(
            analyzer.analyze(
                "방금 만든 보고서를 편집해줘",
                context,
            )
        )

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

        self.assertEqual(4, migrated["schema_version"])
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

    def test_approval_rejects_tampered_step_contract_before_any_writer(self):
        mutations = (
            ("dependency", "depends_on", ["create_powerpoint_summary"]),
            ("effect", "effect", "read_only_analysis"),
            ("idempotency", "idempotency_key", "0" * 64),
        )
        for label, field, value in mutations:
            with self.subTest(label=label):
                plan = self.executor.prepare(self.source)
                plan["step_contracts"]["create_word_report"][field] = value

                with self.assertRaisesRegex(Exception, "단계 실행 계약"):
                    self.executor.start(plan)

                self.assertFalse(
                    self.executor._path(plan["workflow_id"]).exists()
                )

        self.assertEqual(0, self.analyzer.calls)
        self.assertEqual(0, self.word.calls)
        self.assertEqual(0, self.ppt.calls)

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

    def test_schema_three_state_backfills_contract_and_resumes_without_duplicates(self):
        state = self.executor.prepare(self.source)
        with self.assertRaises(WorkflowExecutionError):
            self.executor.start(state)
        failed = self.executor.load(state["workflow_id"])
        failed["schema_version"] = 3
        failed.pop("step_contract_schema_version")
        failed.pop("step_contracts")
        self.executor._save(failed)

        migrated = self.executor.load(state["workflow_id"])
        self.assertEqual(4, migrated["schema_version"])
        self.assertEqual(3, migrated["migrated_from_schema"])
        self.assertEqual(1, migrated["step_contract_schema_version"])
        self.assertEqual(migrated["step_order"], list(migrated["step_contracts"]))

        completed = self.executor.run(state["workflow_id"])

        self.assertTrue(completed["step_contracts_verified"])
        self.assertEqual(3, completed["step_contract_count"])
        self.assertEqual(1, self.analyzer.calls)
        self.assertEqual(1, self.word.calls)
        self.assertEqual(2, self.ppt.calls)

    def test_current_state_with_missing_contract_fails_closed(self):
        missing_field_sets = (
            ("step_contracts",),
            ("step_contract_schema_version", "step_contracts"),
        )
        for fields in missing_field_sets:
            with self.subTest(fields=fields):
                state = self.executor.prepare(self.source)
                for field in fields:
                    state.pop(field)
                self.executor._save(state)

                with self.assertRaisesRegex(Exception, "단계 실행 계약이 불완전"):
                    self.executor.load(state["workflow_id"])

    def test_stored_workflow_identity_mismatch_fails_closed(self):
        state = self.executor.prepare(self.source)
        state["status"] = "failed"
        self.executor._save(state)
        path = self.executor._path(state["workflow_id"])
        payload = path.read_text(encoding="utf-8")
        path.write_text(
            payload.replace(state["workflow_id"], "f" * 32, 1),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(Exception, "파일 ID와 일치하지"):
            self.executor.load(state["workflow_id"])

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

    def test_approval_rejects_tampered_join_plan_before_any_step(self):
        state = self.executor.prepare(
            self.source,
            join_plan=self._join_plan(),
        )
        state["join_plan"]["unexpected"] = "tampered"

        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "두 시트명·두 키·결합 방식과",
        ):
            self.executor.start(state)

        aggregate_plan = self._join_plan()
        aggregate_plan["right_aggregation"] = {
            "column": "매출",
            "function": "sum",
        }
        aggregate_state = self.executor.prepare(
            self.source,
            join_plan=aggregate_plan,
        )
        aggregate_state["join_plan"]["right_aggregation"][
            "function"
        ] = "median"

        with self.assertRaisesRegex(
            WorkflowJoinValidationError,
            "합계·평균·건수",
        ):
            self.executor.start(aggregate_state)

        self.assertEqual(0, self.analyzer.calls)
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_multi_aggregation_plan_is_bounded_and_deduplicated(self):
        cases = (
            (
                [
                    {"column": f"값{index}", "function": "sum"}
                    for index in range(6)
                ],
                "1~5개",
            ),
            (
                [
                    {"column": "매출", "function": "sum"},
                    {"column": "매출", "function": "sum"},
                ],
                "중복 지정",
            ),
            (
                [{"column": "매출", "function": "median"}],
                "합계·평균·건수",
            ),
        )
        for side in ("left", "right"):
            for aggregations, message in cases:
                plan = self._join_plan()
                plan[f"{side}_aggregation"] = aggregations
                with self.subTest(side=side, message=message):
                    with self.assertRaisesRegex(
                        WorkflowJoinValidationError, message
                    ):
                        self.executor.prepare(self.source, join_plan=plan)

    def test_source_scope_is_bounded_and_revalidated_before_any_step(self):
        state = self.executor.prepare(
            self.source,
            source_scope={
                "kind": "range",
                "sheet_name": "매출",
                "address": "$A$1:$B$3",
            },
        )
        self.assertEqual("A1:B3", state["source_scope"]["address"])
        state["source_scope"]["address"] = "A1:AE3"

        with self.assertRaisesRegex(
            WorkflowSourceScopeValidationError,
            "30열",
        ):
            self.executor.start(state)

        with self.assertRaisesRegex(
            WorkflowSourceScopeValidationError,
            "머리글 1행",
        ):
            self.executor.prepare(
                self.source,
                source_scope={
                    "kind": "range",
                    "sheet_name": "매출",
                    "address": "A1:B1",
                },
            )

        with self.assertRaisesRegex(
            WorkflowSourceScopeValidationError,
            "함께 사용할 수 없습니다",
        ):
            self.executor.prepare(
                self.source,
                join_plan=self._join_plan(),
                source_scope={
                    "kind": "range",
                    "sheet_name": "매출",
                    "address": "A1:B3",
                },
            )

        self.assertEqual(0, self.analyzer.calls)
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_legacy_preview_is_never_resumable_and_retention_removes_it(self):
        state = self.executor.prepare(self.source)
        self.executor._save(state)
        self.assertIsNone(self.executor.latest_for_source(self.source))
        self.assertEqual(1, self.executor.cleanup_stale_previews(max_age_days=0))
        self.assertFalse(self.executor._path(state["workflow_id"]).exists())

    def test_recent_artifact_requires_newest_completed_unchanged_file(self):
        self.ppt.fail_times = 0
        first_plan = self.executor.prepare(self.source)
        first = self.executor.start(first_plan)
        report = self.executor.latest_verified_artifact(self.source, "report")
        presentation = self.executor.latest_verified_artifact(
            self.source, "presentation"
        )

        self.assertEqual(first["workflow_id"], report["workflow_id"])
        self.assertEqual("word_report", report["artifact_kind"])
        self.assertEqual(first["output_paths"]["report"], report["path"])
        self.assertEqual(
            first["output_paths"]["presentation"], presentation["path"]
        )

        second_plan = self.executor.prepare(self.source, report_format="both")
        second = self.executor.start(second_plan)
        with self.assertRaisesRegex(WorkflowError, "Word와 한글"):
            self.executor.latest_verified_artifact(self.source, "report")
        word = self.executor.latest_verified_artifact(
            self.source, "word_report"
        )
        self.assertEqual(second["workflow_id"], word["workflow_id"])

        Path(word["path"]).write_bytes(b"user-modified-report")
        with self.assertRaisesRegex(WorkflowError, "이동·수정·삭제"):
            self.executor.latest_verified_artifact(
                self.source, "word_report"
            )

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

    def test_approved_powerpoint_formatting_defaults_apply_by_text_role(self):
        title = SimpleNamespace(
            Font=SimpleNamespace(Bold=0, Size=28.0),
            ParagraphFormat=SimpleNamespace(Alignment=1),
        )
        body = SimpleNamespace(
            Font=SimpleNamespace(Bold=0, Size=18.0),
            ParagraphFormat=SimpleNamespace(Alignment=1),
        )
        preferences = {
            "emphasis_style": "bold",
            "font_scale": "larger",
            "paragraph_align": "center",
        }

        title_applied = PowerPointSummaryWriter._apply_formatting_preferences(
            title, preferences, role="title"
        )
        body_applied = PowerPointSummaryWriter._apply_formatting_preferences(
            body, preferences, role="body"
        )

        self.assertEqual(-1, title.Font.Bold)
        self.assertEqual(36.0, title.Font.Size)
        self.assertEqual(2, title.ParagraphFormat.Alignment)
        self.assertEqual(-1, body.Font.Bold)
        self.assertEqual(24.0, body.Font.Size)
        self.assertEqual(2, body.ParagraphFormat.Alignment)
        self.assertEqual(preferences, title_applied)
        self.assertEqual(preferences, body_applied)

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
