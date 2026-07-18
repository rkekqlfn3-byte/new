import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.app_actions import PreparedAction
from engine.edit_mode import EditModeController, EditSessionManager
from engine.parser import CommandParser


def _digest(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest().upper()


class FakeLayoutManager:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class FakeIntakeManager:
    def __init__(self, path, app_type):
        self.path = str(Path(path).resolve())
        self.app_type = app_type

    def connect_file(self, file_path):
        return {
            "app_type": self.app_type,
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": 10,
            "active_container": None,
            "selection_reference": None,
        }


class StaticContextManager:
    def __init__(self, app_type, text):
        self.app_type = app_type
        self.text = text

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        if self.app_type == "word":
            target = {"start": 4, "end": 4 + len(self.text), "style_name": "본문"}
            selection_kind = "text"
            reference = f"4:{4 + len(self.text)}"
        elif self.app_type == "hwp":
            target = {
                "coordinates": [0, 0, 4, 0, 0, 4 + len(self.text)],
                "position": [0, 0, 4],
            }
            selection_kind = "text"
            reference = (
                "selected:0:0:4:0:0:"
                f"{4 + len(self.text)}"
            )
        else:
            target = {
                "slide_number": 2,
                "slide_id": 200,
                "shape_id": 7,
                "shape_count": 1,
                "shape_name": "제목 1",
                "placeholder_type": 1,
                "left": 20.0,
                "top": 20.0,
                "width": 300.0,
                "height": 50.0,
                "font_size": 28.0,
            }
            selection_kind = "shapes"
            reference = "제목 1"
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": self.app_type,
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": "C" * 64,
            "active_container": (
                "슬라이드 2" if self.app_type == "powerpoint" else None
            ),
            "selection_reference": reference,
            "selection_kind": selection_kind,
            "target": target,
            "selected_text_preview": self.text,
            "selected_text_length": len(self.text),
            "selected_text_digest": _digest(self.text),
            "cursor_reference": reference,
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-16T12:00:00+09:00",
        }


class FakeNativeAdapter:
    def __init__(self, path, context_manager):
        self.path = str(Path(path).resolve())
        self.context_manager = context_manager
        self.executed = 0

    def prepare(self, operation, params):
        context = self.context_manager.capture({
            "session_id": "unused",
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "document_fingerprint": "unused",
        })
        target = context["target"]
        current = {
            "selected_length": context["selected_text_length"],
            "selected_digest": context["selected_text_digest"],
            "selected_preview": context["selected_text_preview"],
        }
        native_params = dict(params)
        if self.context_manager.app_type == "word":
            native_params.update({"start": target["start"], "end": target["end"]})
            current.update({"has_selection": True, "in_table": False, "table": {}})
            sheet = "현재 문서"
        elif self.context_manager.app_type == "hwp":
            current.update({
                "has_selection": True,
                "coordinates": list(target["coordinates"]),
                "position": list(target["position"]),
            })
            sheet = "현재 문서"
        else:
            current.update({
                "slide_number": target["slide_number"],
                "slide_id": target["slide_id"],
                "shape_id": target["shape_id"],
                "selection_type": 2,
            })
            sheet = "슬라이드 2"
        return PreparedAction(
            app=self.context_manager.app_type,
            operation=operation,
            document_id=self.path,
            workbook_name=Path(self.path).name,
            sheet=sheet,
            target=context["selection_reference"],
            params=native_params,
            current_state=current,
            estimated_changes=1,
            destructive=False,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def execute(self, prepared):
        self.executed += 1
        return {"success": True, "verified": True, "changed": True}


class FakeRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, target):
        return self.adapter


class Stage6EditFlowTests(unittest.TestCase):
    def _run_flow(self, app_type, suffix, command):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / f"stage6{suffix}"
            path.write_bytes(b"fixture")
            context = StaticContextManager(
                app_type,
                "현재 제목" if app_type == "powerpoint" else "현재 문장",
            )
            native = FakeNativeAdapter(path, context)
            controller = EditModeController(
                intake_manager=FakeIntakeManager(path, app_type),
                session_manager=EditSessionManager(),
                layout_manager=FakeLayoutManager(),
                context_manager=context,
                native_action_registry=FakeRegistry(native),
            )
            parser = CommandParser(edit_mode_controller=controller)
            session = controller.connect_file(str(path))
            result = parser.execute_command_result(
                command,
                mode="edit",
                session_id=f"chat-stage6-{app_type}",
                edit_context={
                    "edit_session_id": session["session_id"],
                    "document_fingerprint": session["document_fingerprint"],
                    "context_fingerprint": "C" * 64,
                    "request_id": f"request-{app_type}",
                },
            )
            self.assertEqual("confirmation_required", result["status"])
            self.assertEqual(0, native.executed)
            confirmation = result["data"]["confirmation"]
            applied = parser.resolve_pending_confirmation(
                f"chat-stage6-{app_type}",
                confirmation_id=confirmation["confirmation_id"],
                option_id="apply",
            )
            self.assertTrue(applied["success"])
            self.assertTrue(applied["verified"])
            self.assertEqual(1, native.executed)
            self.assertEqual("ready", controller.status()["session"]["state"])

    def test_word_and_powerpoint_use_preview_approval_execute_verify(self):
        cases = (
            ("word", ".docx", '선택 문장을 "새 문장"으로 바꿔줘'),
            ("powerpoint", ".pptx", '제목을 "새 제목"으로 바꿔줘'),
        )
        for app_type, suffix, command in cases:
            with self.subTest(app_type=app_type):
                self._run_flow(app_type, suffix, command)


if __name__ == "__main__":
    unittest.main()
