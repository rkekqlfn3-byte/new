import hashlib
import json
import unittest

from engine.app_actions import PreparedAction
from engine.edit_mode import (
    EditRequest,
    Stage6EditError,
    Stage6NativeEditAdapter,
    StructuredStage6IntentAnalyzer,
)


def _digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest().upper()


class StructuredStage6IntentAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StructuredStage6IntentAnalyzer()
        self.word_text = "이 문장은 안전하게 바꿔야 해요."
        self.word = {
            "app_type": "word",
            "selection_kind": "text",
            "selected_text_preview": self.word_text,
            "selected_text_length": len(self.word_text),
            "target": {
                "start": 10,
                "end": 10 + len(self.word_text),
                "style_name": "본문",
                "font_size": 10.0,
                "bold": 0,
                "paragraph_alignment": 0,
            },
        }
        self.powerpoint = {
            "app_type": "powerpoint",
            "selection_kind": "shapes",
            "selected_text_preview": "현재 제목",
            "selected_text_length": 5,
            "target": {
                "slide_number": 2,
                "slide_id": 200,
                "shape_id": 7,
                "shape_name": "제목 1",
                "placeholder_type": 1,
                "left": 10.0,
                "top": 20.0,
                "width": 300.0,
                "height": 50.0,
                "font_size": 28.0,
                "bold": -1,
                "paragraph_alignment": 2,
            },
        }

    def test_word_commands_map_to_bounded_operations(self):
        cases = {
            '선택 문장을 "결과 문장"으로 바꿔줘': "replace_selection",
            "굵게 해줘": "set_text_format",
            "글자 크기 12": "set_text_format",
            "조금 크게": "set_text_format",
            "제목을 조금 더 크게": "set_text_format",
            "가운데 정렬": "set_paragraph_format",
            "자연스럽게 바꿔줘": "replace_selection",
            "저장해줘": "save_document",
            "현재 스타일 알려줘": "inspect_context",
            "현재 서식 확인해줘": "inspect_context",
            "선택 내용 읽어줘": "read_selection",
        }
        for command, operation in cases.items():
            with self.subTest(command=command):
                intent = self.analyzer.analyze(
                    command,
                    self.word,
                    selection_reader=lambda: self.word_text,
                )
                self.assertEqual(operation, intent.operation)

    def test_powerpoint_commands_map_to_bounded_operations(self):
        cases = {
            '제목을 "새 제목"으로 바꿔줘': "replace_shape_text",
            "제목 조금 크게": "set_text_format",
            "굵게 해줘": "set_text_format",
            "가운데 정렬": "set_text_alignment",
            "Shape를 오른쪽으로 이동": "move_shape",
            "도형을 크게 키워줘": "resize_shape",
            "앞 슬라이드와 같은 스타일로 맞춰줘": "match_previous_style",
            "현재 placeholder 스타일 알려줘": "inspect_context",
            "선택 내용 보여줘": "read_selection",
        }
        for command, operation in cases.items():
            with self.subTest(command=command):
                self.assertEqual(
                    operation,
                    self.analyzer.analyze(command, self.powerpoint).operation,
                )

    def test_shape_resize_and_title_font_growth_are_not_confused(self):
        title = self.analyzer.analyze("제목 조금 크게", self.powerpoint)
        shape = self.analyzer.analyze("도형 크기를 크게", self.powerpoint)
        self.assertEqual("set_text_format", title.operation)
        self.assertEqual(2.0, title.params["font_size_delta"])
        self.assertEqual("resize_shape", shape.operation)


class FakeContextManager:
    def __init__(self, context):
        self.context = dict(context)

    def capture(self, session):
        return dict(self.context)


class FakeStage6Native:
    def __init__(self, app, path, context):
        self.app = app
        self.path = path
        self.context = context
        self.prepared_params = None
        self.shape_id_offset = 0

    def prepare(self, operation, params):
        self.prepared_params = dict(params)
        target = dict(self.context.get("target") or {})
        text_length = int(self.context.get("selected_text_length") or 0)
        current = {
            "selected_length": text_length,
            "selected_digest": self.context.get("selected_text_digest"),
            "selected_preview": self.context.get("selected_text_preview"),
        }
        native_params = dict(params)
        if self.app == "word":
            native_params.update({
                "start": target.get("start"),
                "end": target.get("end"),
            })
            current.update({"has_selection": True, "in_table": False, "table": {}})
            sheet = "현재 문서"
        else:
            current.update({
                "slide_number": target.get("slide_number"),
                "slide_id": target.get("slide_id"),
                "shape_id": int(target.get("shape_id") or 0) + self.shape_id_offset,
                "selection_type": 3 if self.context.get("selection_kind") == "text" else 2,
            })
            sheet = f"슬라이드 {target.get('slide_number')}"
        return PreparedAction(
            app=self.app,
            operation=operation,
            document_id=self.path,
            workbook_name=self.path.rsplit("\\", 1)[-1],
            sheet=sheet,
            target="exact target",
            params=native_params,
            current_state=current,
            estimated_changes=1,
            destructive=False,
            reversible=operation != "save_document",
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def read_selection(self, document_path):
        target = dict(self.context.get("target") or {})
        return {
            "document_id": document_path,
            "start": target.get("start"),
            "end": target.get("end"),
            "has_selection": True,
            "text": self.context.get("full_selected_text"),
        }


class Stage6NativeEditAdapterTests(unittest.TestCase):
    def _request(self):
        return EditRequest(
            text='선택 문장을 "교체" 로 바꿔줘',
            edit_session_id="stage6-session",
            document_fingerprint="A" * 64,
        )

    def test_word_prepared_action_is_exact_and_json_only(self):
        path = r"C:\data\report.docx"
        text = "선택 문장"
        context = {
            "app_type": "word",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "selection_kind": "text",
            "selection_reference": "10:15",
            "selected_text_preview": text,
            "selected_text_length": len(text),
            "selected_text_digest": _digest(text),
            "target": {"start": 10, "end": 15},
        }
        native = FakeStage6Native("word", path, context)
        adapter = Stage6NativeEditAdapter(
            {"app_type": "word", "file_path": path},
            FakeContextManager(context),
            native,
        )
        prepared = adapter.prepare(self._request(), context)
        self.assertEqual(path, native.prepared_params["document_path"])
        self.assertEqual(10, prepared.target["context_target"]["start"])
        json.dumps(prepared.to_dict(), ensure_ascii=False)

    def test_powerpoint_different_shape_is_blocked(self):
        path = r"C:\data\deck.pptx"
        text = "현재 제목"
        context = {
            "app_type": "powerpoint",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "selection_kind": "shapes",
            "selection_reference": "제목 1",
            "selected_text_preview": text,
            "selected_text_length": len(text),
            "selected_text_digest": _digest(text),
            "target": {
                "slide_number": 2,
                "slide_id": 200,
                "shape_id": 7,
            },
        }
        native = FakeStage6Native("powerpoint", path, context)
        native.shape_id_offset = 1
        adapter = Stage6NativeEditAdapter(
            {"app_type": "powerpoint", "file_path": path},
            FakeContextManager(context),
            native,
        )
        request = EditRequest(
            text='제목을 "새 제목"으로 바꿔줘',
            edit_session_id="stage6-session",
            document_fingerprint="A" * 64,
        )
        with self.assertRaises(Stage6EditError):
            adapter.prepare(request, context)

    def test_nonreversible_word_save_never_claims_rollback(self):
        path = r"C:\data\report.docx"
        context = {
            "app_type": "word",
            "document_fingerprint": "A" * 64,
            "context_fingerprint": "C" * 64,
            "selection_kind": "cursor",
            "selection_reference": "0:0",
            "selected_text_preview": "",
            "selected_text_length": 0,
            "selected_text_digest": _digest(""),
            "target": {"start": 0, "end": 0},
        }
        native = FakeStage6Native("word", path, context)
        adapter = Stage6NativeEditAdapter(
            {"app_type": "word", "file_path": path},
            FakeContextManager(context),
            native,
        )
        prepared = adapter.prepare(
            EditRequest(
                text="저장해줘",
                edit_session_id="stage6-session",
                document_fingerprint="A" * 64,
            ),
            context,
        )
        self.assertFalse(adapter.rollback(prepared))


if __name__ == "__main__":
    unittest.main()
