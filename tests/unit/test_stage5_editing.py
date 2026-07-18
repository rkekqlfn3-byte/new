import hashlib
import json
import unittest

from engine.app_actions import PreparedAction
from engine.edit_mode import (
    EditRequest,
    Stage5EditError,
    Stage5NativeEditAdapter,
    StructuredEditIntentAnalyzer,
)


class StructuredEditIntentAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StructuredEditIntentAnalyzer()
        self.excel = {
            "app_type": "excel",
            "selection_kind": "range",
            "selection_reference": "A2:A5",
            "selected_text_preview": "10 · 20 · 30 · 40",
        }

    def test_representative_excel_commands_map_to_allowlisted_operations(self):
        cases = {
            "42 입력해줘": "write_cell",
            '"=SUM(A1:A3)" 입력해줘': "write_cell",
            "합계 내줘": "sum_column_to_cell",
            "평균 내줘": "write_cell",
            "굵게 해줘": "format_range",
            "글자 크기 12": "format_range",
            '"A"를 "B"로 바꿔줘': "find_replace",
            "A열 기준 오름차순 정렬": "sort_range",
            '"상태" 열을 "완료"로 필터해줘': "filter_range",
            "필터 해제": "filter_range",
            "2개 행 추가": "insert_rows",
            "열 삽입": "insert_columns",
            "선택 내용 보여줘": "read_selection",
        }
        for command, operation in cases.items():
            with self.subTest(command=command):
                context = dict(self.excel)
                if command in {"42 입력해줘", '"=SUM(A1:A3)" 입력해줘'}:
                    context["selection_reference"] = "A2"
                self.assertEqual(
                    operation,
                    self.analyzer.analyze(command, context).operation,
                )

    def test_hwp_commands_include_selection_transform_and_table_cell_input(self):
        selected = "이 문장은 꽤 길어서 조금 더 간결하게 바꿀 수 있어요. 두 번째 문장입니다."
        context = {
            "app_type": "hwp",
            "selection_kind": "text",
            "selected_text_preview": selected,
        }
        cases = {
            '선택 문장을 "결과 문장"으로 바꿔줘': "insert_text",
            "조금 줄여줘": "insert_text",
            "보고서체로 바꿔줘": "insert_text",
            "굵게 해줘": "set_text_format",
            "글자 크기 11": "set_text_format",
            "글자 크기를 조금 작게": "set_text_format",
            "가운데 정렬": "set_paragraph_format",
            '"이"를 "해당"으로 바꿔줘': "find_replace",
            '표 셀에 "완료" 입력해줘': "insert_text",
            "선택 내용 읽어줘": "read_selection",
        }
        for command, operation in cases.items():
            with self.subTest(command=command):
                intent = self.analyzer.analyze(
                    command,
                    context,
                    selection_reader=lambda: selected,
                )
                self.assertEqual(operation, intent.operation)

        relative = self.analyzer.analyze(
            "글자 크기를 조금 작게",
            context,
            selection_reader=lambda: selected,
        )
        self.assertEqual(-2.0, relative.params["font_size_delta"])
        shortened = self.analyzer.analyze(
            "조금 줄여줘",
            context,
            selection_reader=lambda: selected,
        )
        self.assertEqual("insert_text", shortened.operation)
        self.assertNotIn("font_size_delta", shortened.params)

    def test_write_to_an_unselected_cell_is_blocked(self):
        with self.assertRaises(Stage5EditError):
            self.analyzer.analyze("B9에 42 입력해줘", self.excel)


class FakeContextManager:
    def __init__(self, context):
        self.context = dict(context)

    def capture(self, session):
        return dict(self.context)


class FakeNativeAdapter:
    def __init__(self, document_id):
        self.document_id = document_id

    def prepare(self, operation, params):
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=self.document_id,
            workbook_name="book.xlsx",
            sheet="Sheet1",
            target=params.get("cell") or params.get("range") or "A2:A5",
            params=dict(params),
            current_state={"value": None},
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )


class Stage5NativeEditAdapterTests(unittest.TestCase):
    def test_prepared_action_is_json_only_and_bound_to_exact_document_and_sheet(self):
        path = r"C:\data\book.xlsx"
        context = {
            "app_type": "excel",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "active_container": "Sheet1",
            "selection_kind": "range",
            "selection_reference": "A2:A5",
            "target": {"sheet_name": "Sheet1", "address": "A2:A5"},
            "selected_text_preview": "10 · 20",
        }
        session = {
            "session_id": "edit-session-1",
            "app_type": "excel",
            "file_path": path,
        }
        adapter = Stage5NativeEditAdapter(
            session,
            FakeContextManager(context),
            FakeNativeAdapter(path),
        )
        request = EditRequest(
            text="합계 내줘",
            edit_session_id="edit-session-1",
            document_fingerprint="A" * 64,
            request_id="request-1",
        )

        prepared = adapter.prepare(request, context)
        encoded = json.dumps(prepared.to_dict(), ensure_ascii=False)

        self.assertIn("sum_column_to_cell", encoded)
        self.assertNotIn("FakeNativeAdapter", encoded)
        self.assertTrue(prepared.requires_approval)

    def test_hwp_selection_reader_requires_full_text_digest_match(self):
        text = "선택된 전체 문장입니다."
        path = r"C:\data\document.hwp"

        class Native:
            def read_selection(self):
                return {
                    "document_id": path,
                    "has_selection": True,
                    "text": text + " 변경",
                }

        context = {
            "app_type": "hwp",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "selection_kind": "text",
            "selected_text_length": len(text),
            "selected_text_digest": hashlib.sha256(text.encode()).hexdigest().upper(),
        }
        adapter = Stage5NativeEditAdapter(
            {"app_type": "hwp", "file_path": path},
            FakeContextManager(context),
            Native(),
        )
        request = EditRequest(
            text="조금 줄여줘",
            edit_session_id="edit-session-1",
            document_fingerprint="A" * 64,
        )
        with self.assertRaises(Stage5EditError):
            adapter.prepare(request, context)

    def test_hwp_lost_native_selection_never_becomes_cursor_insertion(self):
        text = "반드시 이 선택 영역만 바꿉니다."
        path = r"C:\data\document.hwp"

        class Native:
            def read_selection(self):
                return {
                    "document_id": path,
                    "has_selection": True,
                    "text": text,
                }

            def prepare(self, operation, params):
                return PreparedAction(
                    app="hwp",
                    operation=operation,
                    document_id=path,
                    workbook_name="document.hwp",
                    sheet="현재 문서",
                    target="커서 위치 0:0:10",
                    params=dict(params),
                    current_state={
                        "has_selection": False,
                        "selected_length": 0,
                    },
                    estimated_changes=1,
                    destructive=False,
                    reversible=True,
                    verification_method="read_back",
                    context_fingerprint="B" * 64,
                    prepared_at="2026-07-16T12:00:00+09:00",
                )

        context = {
            "app_type": "hwp",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "selection_kind": "text",
            "selected_text_length": len(text),
            "selected_text_digest": hashlib.sha256(text.encode()).hexdigest().upper(),
            "target": {},
        }
        adapter = Stage5NativeEditAdapter(
            {"app_type": "hwp", "file_path": path},
            FakeContextManager(context),
            Native(),
        )
        request = EditRequest(
            text='선택 문장을 "교체 문장"으로 바꿔줘',
            edit_session_id="edit-session-1",
            document_fingerprint="A" * 64,
        )
        with self.assertRaises(Stage5EditError):
            adapter.prepare(request, context)


if __name__ == "__main__":
    unittest.main()
