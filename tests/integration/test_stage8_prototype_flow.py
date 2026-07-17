import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.app_actions import PreparedAction
from engine.edit_mode import (
    EditModeController,
    EditRequest,
    EditSessionManager,
    EditSessionNotFound,
    EditSessionStale,
)
from engine.parser import CommandParser


APP_BY_SUFFIX = {
    ".xlsx": "excel",
    ".hwp": "hwp",
    ".docx": "word",
    ".pptx": "powerpoint",
}


def digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest().upper()


class SwitchingIntakeManager:
    def connect_file(self, file_path):
        path = Path(file_path).resolve()
        app_type = APP_BY_SUFFIX[path.suffix.casefold()]
        return {
            "app_type": app_type,
            "file_path": str(path),
            "document_name": path.name,
            "window_handle": 800,
            "active_container": "Sheet1" if app_type == "excel" else None,
            "selection_reference": "A2:A3" if app_type == "excel" else "selection",
        }


class NoLayout:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class SwitchingContextManager:
    def __init__(self):
        self.versions = {app: 1 for app in APP_BY_SUFFIX.values()}
        self.closed_paths = set()
        self.hwp_text = "통합 검증을 위해 선택한 문장을 조금 더 간결하게 정리합니다."
        self.word_text = "통합 검증 제목"
        self.ppt_text = "현재 슬라이드 제목"

    def advance(self, app_type):
        self.versions[app_type] += 1

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        path = str(Path(value["file_path"]).resolve())
        if path in self.closed_paths:
            raise RuntimeError("연결된 문서가 닫혔습니다.")
        app_type = value["app_type"]
        common = {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": app_type,
            "file_path": path,
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": f"{self.versions[app_type]:064X}",
            "read_only": False,
            "modified": True,
            "captured_at": "2026-07-17T09:00:00+09:00",
        }
        if app_type == "excel":
            return {
                **common,
                "active_container": "Sheet1",
                "selection_reference": "A2:A3",
                "selection_kind": "range",
                "target": {"sheet_name": "Sheet1", "address": "A2:A3"},
                "selected_text_preview": "10, 20",
                "selected_text_length": 6,
                "selected_text_digest": digest("10, 20"),
                "cursor_reference": "A2",
            }
        if app_type == "hwp":
            return {
                **common,
                "active_container": "현재 문서",
                "selection_reference": "selected:0:0:16:0:0:48",
                "selection_kind": "text",
                "target": {"coordinates": [0, 0, 16, 0, 0, 48]},
                "selected_text_preview": self.hwp_text,
                "selected_text_length": len(self.hwp_text),
                "selected_text_digest": digest(self.hwp_text),
                "cursor_reference": "0:0:48",
            }
        if app_type == "word":
            return {
                **common,
                "active_container": "현재 문서",
                "selection_reference": f"0:{len(self.word_text)}",
                "selection_kind": "text",
                "target": {
                    "start": 0,
                    "end": len(self.word_text),
                    "bold": 0,
                    "font_size": 10.0,
                    "paragraph_alignment": 0,
                },
                "selected_text_preview": self.word_text,
                "selected_text_length": len(self.word_text),
                "selected_text_digest": digest(self.word_text),
                "cursor_reference": "0",
            }
        return {
            **common,
            "active_container": "슬라이드 2",
            "selection_reference": "slide:2:shape:7",
            "selection_kind": "shapes",
            "target": {
                "slide_number": 2,
                "slide_id": 200,
                "shape_id": 7,
                "shape_name": "제목 2",
                "placeholder_type": 1,
                "left": 50.0,
                "top": 50.0,
                "width": 300.0,
                "height": 50.0,
                "font_size": 24.0,
                "bold": 0,
                "paragraph_alignment": 1,
            },
            "selected_text_preview": self.ppt_text,
            "selected_text_length": len(self.ppt_text),
            "selected_text_digest": digest(self.ppt_text),
            "cursor_reference": "slide:2",
        }


class IntegratedNativeAdapter:
    def __init__(self, app_type, context):
        self.app_type = app_type
        self.context = context
        self.executed = []
        self.fail_next = False

    def read_selection(self, document_path=None):
        text = self.context.hwp_text if self.app_type == "hwp" else self.context.word_text
        result = {
            "document_id": str(Path(document_path or self.path).resolve()),
            "has_selection": True,
            "text": text,
            "text_length": len(text),
            "text_digest": digest(text),
        }
        if self.app_type == "word":
            result.update({"start": 0, "end": len(text)})
        return result

    def prepare(self, operation, params):
        values = dict(params)
        path = str(Path(values.get("document_path") or self.path).resolve())
        state = {"value": None, "formula": None}
        target = "A4" if operation == "sum_column_to_cell" else "selection"
        if self.app_type == "hwp":
            state = {
                "has_selection": True,
                "selected_length": len(self.context.hwp_text),
                "selected_digest": digest(self.context.hwp_text),
                "selected_preview": self.context.hwp_text,
            }
            values["original_text"] = self.context.hwp_text
        elif self.app_type == "word":
            values.update({"start": 0, "end": len(self.context.word_text)})
            state = {
                "selected_length": len(self.context.word_text),
                "selected_digest": digest(self.context.word_text),
            }
        elif self.app_type == "powerpoint":
            state = {
                "slide_number": 2,
                "slide_id": 200,
                "shape_id": 7,
                "selection_type": 2,
                "selected_length": len(self.context.ppt_text),
                "selected_digest": digest(self.context.ppt_text),
            }
        return PreparedAction(
            app=self.app_type,
            operation=operation,
            document_id=path,
            workbook_name=Path(path).name,
            sheet="Sheet1" if self.app_type == "excel" else "현재 문서",
            target=target,
            params=values,
            current_state=state,
            estimated_changes=1,
            destructive=True,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="F" * 64,
            prepared_at="2026-07-17T09:00:00+09:00",
        )

    def execute(self, prepared):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected native failure")
        self.executed.append(prepared.operation)
        self.context.advance(self.app_type)
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": prepared.operation,
            "target": prepared.target,
        }

    def undo(self, prepared, record):
        self.context.advance(self.app_type)
        return {"verified": True, "changed": True, "operation": "undo_last_edit"}


class Registry:
    def __init__(self, adapters):
        self.adapters = adapters

    def get(self, target):
        return self.adapters[str(target).casefold()]


class PrototypeStage8FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.paths = {}
        for suffix, app_type in APP_BY_SUFFIX.items():
            path = Path(self.temp_dir.name) / f"stage8{suffix}"
            path.write_bytes(f"fixture-{app_type}".encode())
            self.paths[app_type] = path
        self.context = SwitchingContextManager()
        self.adapters = {
            app: IntegratedNativeAdapter(app, self.context)
            for app in APP_BY_SUFFIX.values()
        }
        for app, adapter in self.adapters.items():
            adapter.path = str(self.paths[app])
        self.sessions = EditSessionManager()
        self.controller = EditModeController(
            intake_manager=SwitchingIntakeManager(),
            session_manager=self.sessions,
            layout_manager=NoLayout(),
            context_manager=self.context,
            native_action_registry=Registry(self.adapters),
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(self, session, text, request_id, chat="stage8-chat"):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id=chat,
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": (
                    f"{self.context.versions[session['app_type']]:064X}"
                ),
                "request_id": request_id,
            },
        )

    def approve(self, preview, chat="stage8-chat"):
        confirmation = preview["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            chat,
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

    def test_four_apps_switch_sessions_and_run_representative_natural_edits(self):
        cases = (
            ("excel", "이거 합계 내줘", "sum_column_to_cell"),
            ("hwp", "이 부분 좀 줄여줘", "insert_text"),
            ("word", "제목을 조금 더 크게", "set_text_format"),
            ("powerpoint", "앞 장이랑 같은 형식으로", "match_previous_style"),
        )
        previous = None
        for index, (app_type, command, operation) in enumerate(cases, start=1):
            session = self.controller.connect_file(str(self.paths[app_type]))
            if previous is not None:
                self.assertNotEqual(previous["session_id"], session["session_id"])
                with self.assertRaises((EditSessionStale, EditSessionNotFound)):
                    self.controller.handle(EditRequest(
                        text="아까처럼",
                        edit_session_id=previous["session_id"],
                        document_fingerprint=previous["document_fingerprint"],
                        request_id=f"old-{index}",
                    ))
            preview = self.command(session, command, f"stage8-{app_type}")
            self.assertEqual(
                "confirmation_required",
                preview["status"],
                f"{app_type}: {preview.get('message')}",
            )
            result = self.approve(preview)
            self.assertTrue(result["success"])
            self.assertEqual(operation, result["data"]["operation"])
            self.assertEqual([operation], self.adapters[app_type].executed)
            previous = session

    def test_closed_document_discards_context_and_never_replays_last_edit(self):
        session = self.controller.connect_file(str(self.paths["word"]))
        self.approve(self.command(session, "제목을 조금 크게", "word-first"))
        executed = len(self.adapters["word"].executed)
        self.context.closed_paths.add(str(self.paths["word"].resolve()))

        result = self.command(session, "방금 거 다시", "word-closed")

        self.assertFalse(result["success"])
        self.assertEqual(executed, len(self.adapters["word"].executed))
        status = self.controller.status()["session"]
        self.assertIsNone(status["last_action"])
        self.assertIsNone(status["undo_record"])

    def test_exception_returns_ready_and_a_fresh_request_can_recover(self):
        session = self.controller.connect_file(str(self.paths["excel"]))
        self.adapters["excel"].fail_next = True
        preview = self.command(session, "이거 합계 내줘", "excel-fail")
        failed = self.approve(preview)
        self.assertFalse(failed["success"])
        self.assertEqual("ready", self.controller.status()["session"]["state"])

        recovered = self.approve(
            self.command(session, "이거 합계 내줘", "excel-recover")
        )
        self.assertTrue(recovered["success"])
        self.assertEqual(1, len(self.adapters["excel"].executed))

    def test_same_filename_different_paths_and_restart_do_not_reuse_a_session(self):
        first_dir = Path(self.temp_dir.name) / "one"
        second_dir = Path(self.temp_dir.name) / "two"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "same.docx"
        second = second_dir / "same.docx"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        original = self.controller.connect_file(str(first))
        switched = self.controller.connect_file(str(second))
        self.assertNotEqual(
            original["document_fingerprint"],
            switched["document_fingerprint"],
        )
        with self.assertRaises((EditSessionStale, EditSessionNotFound)):
            self.controller.handle(EditRequest(
                text="제목을 조금 크게",
                edit_session_id=original["session_id"],
                document_fingerprint=original["document_fingerprint"],
                request_id="same-name-old",
            ))

        restarted = EditModeController(
            intake_manager=SwitchingIntakeManager(),
            session_manager=EditSessionManager(),
            layout_manager=NoLayout(),
            context_manager=self.context,
            native_action_registry=Registry(self.adapters),
        )
        self.assertFalse(restarted.status()["connected"])


if __name__ == "__main__":
    unittest.main()
