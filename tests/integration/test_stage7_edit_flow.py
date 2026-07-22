import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.app_actions import PreparedAction
from engine.edit_mode import EditModeController, EditSessionManager
from engine.learning import UserPreferenceLearningManager
from engine.parser import CommandParser


def fingerprint(number):
    return f"{int(number):064X}"


class FakeIntakeManager:
    def __init__(self, path, app_type):
        self.path = str(Path(path).resolve())
        self.app_type = app_type

    def connect_file(self, file_path):
        return {
            "app_type": self.app_type,
            "file_path": self.path,
            "document_name": Path(self.path).name,
            "window_handle": 70,
            "active_container": "Sheet1" if self.app_type == "excel" else "현재 문서",
            "selection_reference": "A2" if self.app_type == "excel" else "선택 텍스트",
        }


class FakeLayoutManager:
    enabled = False

    def arrange(self, session_id, document_handle):
        return {"success": False, "status": "skipped", "arranged": False}

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.enabled


class MutableExcelContext:
    def __init__(self):
        self.version = 1
        self.address = "A2"

    @property
    def context_fingerprint(self):
        return fingerprint(self.version)

    def advance(self):
        self.version += 1

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": "excel",
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": self.context_fingerprint,
            "active_container": "Sheet1",
            "selection_reference": self.address,
            "selection_kind": "range",
            "target": {"sheet_name": "Sheet1", "address": self.address},
            "selected_text_preview": "10",
            "selected_text_length": 2,
            "selected_text_digest": "D" * 64,
            "cursor_reference": self.address,
            "read_only": False,
            "modified": True,
            "captured_at": "2026-07-16T12:00:00+09:00",
        }


class StatefulExcelAdapter:
    def __init__(self, path, context):
        self.path = str(Path(path).resolve())
        self.context = context
        self.executed = 0
        self.undone = 0

    def prepare(self, operation, params):
        target = params.get("cell") or params.get("range") or self.context.address
        return PreparedAction(
            app="excel",
            operation=operation,
            document_id=self.path,
            workbook_name=Path(self.path).name,
            sheet="Sheet1",
            target=target,
            params=dict(params),
            current_state={"value": 10, "formula": None},
            estimated_changes=1,
            destructive=True,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="E" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def execute(self, prepared):
        self.executed += 1
        self.context.advance()
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": prepared.operation,
            "target": prepared.target,
            "after": {"value": prepared.params.get("value")},
        }

    def undo(self, prepared, record):
        self.undone += 1
        self.context.advance()
        return {
            "success": True,
            "verified": True,
            "changed": True,
            "operation": "undo_last_edit",
            "target": prepared.target,
        }


class StaticHwpContext:
    def __init__(self, text):
        self.text = text
        self.context_fingerprint = "A" * 64

    @property
    def digest(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest().upper()

    def capture(self, session):
        value = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        return {
            "schema_version": 1,
            "session_id": value["session_id"],
            "app_type": "hwp",
            "file_path": value["file_path"],
            "document_name": value["document_name"],
            "document_fingerprint": value["document_fingerprint"],
            "context_fingerprint": self.context_fingerprint,
            "active_container": "현재 문서",
            "selection_reference": "선택 텍스트",
            "selection_kind": "text",
            "target": {"selection_reference": "선택 텍스트"},
            "selected_text_preview": self.text[:200],
            "selected_text_length": len(self.text),
            "selected_text_digest": self.digest,
            "cursor_reference": "1:1",
            "read_only": False,
            "modified": False,
            "captured_at": "2026-07-16T12:00:00+09:00",
        }


class PreviewHwpAdapter:
    def __init__(self, path, context):
        self.path = str(Path(path).resolve())
        self.context = context
        self.executed = 0

    def read_selection(self):
        return {
            "document_id": self.path,
            "has_selection": True,
            "text": self.context.text,
            "text_length": len(self.context.text),
            "text_digest": self.context.digest,
            "position": [0, 0, 0],
            "coordinates": [0, 0, 0, 0, 0, len(self.context.text)],
        }

    def prepare(self, operation, params):
        return PreparedAction(
            app="hwp",
            operation=operation,
            document_id=self.path,
            workbook_name=Path(self.path).name,
            sheet="현재 문서",
            target="선택 텍스트",
            params={
                **dict(params),
                "original_text": self.context.text,
            },
            current_state={
                "has_selection": True,
                "selected_length": len(self.context.text),
                "selected_digest": self.context.digest,
                "selected_preview": self.context.text[:200],
            },
            estimated_changes=len(self.context.text),
            destructive=True,
            reversible=True,
            verification_method="read_back",
            context_fingerprint="B" * 64,
            prepared_at="2026-07-16T12:00:00+09:00",
        )

    def execute(self, prepared):
        self.executed += 1
        return {"verified": True, "changed": True}


class FakeRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, target):
        return self.adapter


class Stage7EditFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.temp_dir.name) / "stage7.xlsx"
        self.file.write_bytes(b"fixture")
        self.context = MutableExcelContext()
        self.native = StatefulExcelAdapter(self.file, self.context)
        self.controller = EditModeController(
            intake_manager=FakeIntakeManager(self.file, "excel"),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=self.context,
            native_action_registry=FakeRegistry(self.native),
        )
        self.parser = CommandParser(edit_mode_controller=self.controller)
        self.session = self.controller.connect_file(str(self.file))

    def tearDown(self):
        self.temp_dir.cleanup()

    def command(
        self, text, request_id, chat="chat-stage7", expected_undo_action_id=None
    ):
        return self.parser.execute_command_result(
            text,
            mode="edit",
            session_id=chat,
            edit_context={
                "edit_session_id": self.session["session_id"],
                "document_fingerprint": self.session["document_fingerprint"],
                "context_fingerprint": self.context.context_fingerprint,
                "request_id": request_id,
                "expected_undo_action_id": expected_undo_action_id,
            },
        )

    def apply(self, preview, chat="chat-stage7"):
        confirmation = preview["data"]["confirmation"]
        return self.parser.resolve_pending_confirmation(
            chat,
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

    def test_five_continuous_edits_then_one_verified_undo(self):
        counts = []
        for index, command in enumerate(
            ["42 입력해줘", "아까처럼", "아까처럼", "아까처럼", "아까처럼"],
            start=1,
        ):
            preview = self.command(command, f"stage7-sequence-{index}")
            self.assertEqual("confirmation_required", preview["status"])
            result = self.apply(preview)
            self.assertTrue(result["success"])
            counts.append(result["data"]["sequence_count"])
        self.assertEqual([1, 2, 3, 4, 5], counts)
        self.assertEqual(5, self.native.executed)
        self.assertTrue(self.controller.status()["session"]["undo_record"]["available"])

        undone = self.command("방금 거 취소해", "stage7-undo")
        self.assertTrue(undone["success"])
        self.assertEqual("undo_last_edit", undone["data"]["operation"])
        self.assertEqual(1, self.native.undone)
        status = self.controller.status()["session"]
        self.assertIsNone(status["last_action"])
        self.assertIsNone(status["undo_record"])

    def test_target_change_discards_continuation_and_undo(self):
        self.apply(self.command("42 입력해줘", "stage7-target-1"))
        self.context.address = "B9"
        self.context.advance()

        status = self.controller.status()["session"]

        self.assertIsNone(status["last_action"])
        self.assertIsNone(status["undo_record"])

    def test_stale_undo_button_cannot_undo_latest_edit(self):
        applied = self.apply(self.command("42 입력해줘", "stage7-token-source"))
        self.assertTrue(applied["data"]["undo_available"])
        blocked = self.command(
            "방금 작업 되돌려줘",
            "stage7-stale-button",
            expected_undo_action_id="edit-older-action",
        )
        self.assertFalse(blocked["success"])
        self.assertEqual(0, self.native.undone)
        self.assertTrue(
            self.controller.status()["session"]["undo_record"]["available"]
        )

    def test_rewrite_option_replaces_preview_without_executing_document_write(self):
        hwp_file = Path(self.temp_dir.name) / "stage7.hwp"
        hwp_file.write_bytes(b"fixture")
        text = "이번 분기 실적은 여러 요인 덕분에 크게 개선되었으며 다음 분기에도 성장세가 이어질 전망입니다."
        context = StaticHwpContext(text)
        native = PreviewHwpAdapter(hwp_file, context)
        controller = EditModeController(
            intake_manager=FakeIntakeManager(hwp_file, "hwp"),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=context,
            native_action_registry=FakeRegistry(native),
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(hwp_file))
        edit_context = {
            "edit_session_id": session["session_id"],
            "document_fingerprint": session["document_fingerprint"],
            "context_fingerprint": context.context_fingerprint,
            "request_id": "stage7-rewrite-1",
        }
        preview = parser.execute_command_result(
            "조금 줄여줘",
            mode="edit",
            session_id="chat-stage7-rewrite",
            edit_context=edit_context,
        )
        confirmation = preview["data"]["confirmation"]
        option_ids = [item["id"] for item in confirmation["options"]]
        self.assertEqual(["apply", "rewrite", "cancel"], option_ids)

        rewritten = parser.resolve_pending_confirmation(
            "chat-stage7-rewrite",
            confirmation_id=confirmation["confirmation_id"],
            option_id="rewrite",
        )

        self.assertEqual("confirmation_required", rewritten["status"])
        self.assertNotEqual(
            confirmation["confirmation_id"],
            rewritten["data"]["confirmation"]["confirmation_id"],
        )
        self.assertEqual(0, native.executed)

    def test_free_form_rewrite_feedback_records_only_structured_evidence(self):
        hwp_file = Path(self.temp_dir.name) / "stage7-feedback.hwp"
        hwp_file.write_bytes(b"fixture")
        text = "이번 분기 실적은 여러 요인 덕분에 크게 개선되었으며 다음 분기에도 성장세가 이어질 전망입니다."
        context = StaticHwpContext(text)
        native = PreviewHwpAdapter(hwp_file, context)
        learning_path = Path(self.temp_dir.name) / "feedback-preferences.json"
        learning = UserPreferenceLearningManager(learning_path)
        controller = EditModeController(
            intake_manager=FakeIntakeManager(hwp_file, "hwp"),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=context,
            native_action_registry=FakeRegistry(native),
            user_learning_manager=learning,
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(hwp_file))
        preview = parser.execute_command_result(
            "조금 줄여줘",
            mode="edit",
            session_id="chat-stage7-feedback",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": context.context_fingerprint,
                "request_id": "stage7-feedback-1",
            },
        )
        confirmation = preview["data"]["confirmation"]
        feedback_text = "그거 말고 좀 더 간결하게 다시 작성해줘"

        rewritten = parser.resolve_pending_confirmation(
            "chat-stage7-feedback",
            confirmation_id=confirmation["confirmation_id"],
            user_text=feedback_text,
        )

        self.assertEqual("confirmation_required", rewritten["status"])
        self.assertEqual(0, native.executed)
        feedback = rewritten["data"]["preference_feedback"]
        self.assertTrue(feedback["recorded"])
        self.assertEqual("preview_rewrite", feedback["source"])
        self.assertEqual("report_tone", feedback["preference"])
        self.assertEqual("concise", feedback["value"])
        self.assertEqual("file", feedback["scope_kind"])
        self.assertEqual(1, feedback["evidence_count"])
        self.assertFalse(feedback["needs_confirmation"])
        self.assertFalse(feedback["raw_feedback_stored"])
        self.assertIsNone(
            learning.resolve("report_tone", file_path=hwp_file)
        )
        serialized = learning_path.read_text(encoding="utf-8")
        self.assertNotIn(feedback_text, serialized)

    def test_cancel_reason_can_be_evidence_without_changing_the_document(self):
        hwp_file = Path(self.temp_dir.name) / "stage7-cancel-feedback.hwp"
        hwp_file.write_bytes(b"fixture")
        text = "이번 분기 실적을 길게 설명한 문장입니다."
        context = StaticHwpContext(text)
        native = PreviewHwpAdapter(hwp_file, context)
        learning_path = Path(self.temp_dir.name) / "cancel-feedback-preferences.json"
        learning = UserPreferenceLearningManager(learning_path)
        controller = EditModeController(
            intake_manager=FakeIntakeManager(hwp_file, "hwp"),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=context,
            native_action_registry=FakeRegistry(native),
            user_learning_manager=learning,
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(hwp_file))
        preview = parser.execute_command_result(
            "조금 줄여줘",
            mode="edit",
            session_id="chat-stage7-cancel-feedback",
            edit_context={
                "edit_session_id": session["session_id"],
                "document_fingerprint": session["document_fingerprint"],
                "context_fingerprint": context.context_fingerprint,
                "request_id": "stage7-cancel-feedback-1",
            },
        )
        confirmation = preview["data"]["confirmation"]
        feedback_text = "취소해. 앞으로는 좀 더 친근하게 해줘"

        cancelled = parser.resolve_pending_confirmation(
            "chat-stage7-cancel-feedback",
            confirmation_id=confirmation["confirmation_id"],
            user_text=feedback_text,
        )

        self.assertFalse(cancelled["success"])
        self.assertEqual("user_cancelled", cancelled["error_type"])
        self.assertEqual(0, native.executed)
        feedback = cancelled["data"]["preference_feedback"]
        self.assertTrue(feedback["recorded"])
        self.assertEqual("preview_cancel", feedback["source"])
        self.assertEqual("friendly", feedback["value"])
        self.assertEqual("workflow", feedback["scope_kind"])
        self.assertIsNone(
            learning.resolve("report_tone", workflow_id="business_report")
        )
        self.assertNotIn(
            feedback_text,
            learning_path.read_text(encoding="utf-8"),
        )

    def test_verified_follow_up_correction_becomes_evidence_after_apply_only(self):
        hwp_file = Path(self.temp_dir.name) / "stage7-follow-up-feedback.hwp"
        hwp_file.write_bytes(b"fixture")
        text = "이번 분기 실적은 여러 요인 덕분에 크게 개선되었으며 다음 분기에도 성장세가 이어질 전망입니다."
        context = StaticHwpContext(text)
        native = PreviewHwpAdapter(hwp_file, context)
        learning_path = Path(self.temp_dir.name) / "follow-up-preferences.json"
        learning = UserPreferenceLearningManager(learning_path)
        controller = EditModeController(
            intake_manager=FakeIntakeManager(hwp_file, "hwp"),
            session_manager=EditSessionManager(),
            layout_manager=FakeLayoutManager(),
            context_manager=context,
            native_action_registry=FakeRegistry(native),
            user_learning_manager=learning,
        )
        parser = CommandParser(edit_mode_controller=controller)
        session = controller.connect_file(str(hwp_file))

        def command(value, request_id):
            return parser.execute_command_result(
                value,
                mode="edit",
                session_id="chat-stage7-follow-up-feedback",
                edit_context={
                    "edit_session_id": session["session_id"],
                    "document_fingerprint": session["document_fingerprint"],
                    "context_fingerprint": context.context_fingerprint,
                    "request_id": request_id,
                },
            )

        first = command("조금 줄여줘", "follow-up-base")
        first_confirmation = first["data"]["confirmation"]
        parser.resolve_pending_confirmation(
            "chat-stage7-follow-up-feedback",
            confirmation_id=first_confirmation["confirmation_id"],
            option_id="apply",
        )
        correction = command("너무 길어", "follow-up-correction")
        self.assertEqual("confirmation_required", correction["status"])
        self.assertEqual([], learning.list_candidates(include_observing=True))

        confirmation = correction["data"]["confirmation"]
        completed = parser.resolve_pending_confirmation(
            "chat-stage7-follow-up-feedback",
            confirmation_id=confirmation["confirmation_id"],
            option_id="apply",
        )

        self.assertTrue(completed["success"])
        self.assertEqual(2, native.executed)
        feedback = completed["data"]["preference_feedback"]
        self.assertTrue(feedback["recorded"])
        self.assertEqual("verified_follow_up", feedback["source"])
        self.assertEqual("concise", feedback["value"])
        self.assertEqual(1, feedback["evidence_count"])
        self.assertIn("아직 기본값으로 확정하지 않았습니다", completed["message"])
        self.assertIsNone(learning.resolve("report_tone", file_path=hwp_file))
        self.assertNotIn(
            "너무 길어",
            learning_path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
