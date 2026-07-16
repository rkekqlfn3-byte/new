import unittest
from unittest import mock

from engine.api import command_api
from engine.execution_result import success_result
from engine.parser import CommandParser


FINGERPRINT = "C" * 64
EDIT_CONTEXT = {
    "edit_session_id": "edit-session-1",
    "document_fingerprint": FINGERPRINT,
    "request_id": "request-1",
}


class EditModeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.parser = CommandParser()

    def test_edit_without_session_metadata_never_falls_through_to_command(self):
        self.parser.analyze_command = mock.Mock()
        self.parser.llm_engine.process_command = mock.Mock()

        result = self.parser.execute_command_result(
            "메모장 열어줘",
            mode="edit",
        )

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("edit", result["action"])
        self.parser.analyze_command.assert_not_called()
        self.parser.llm_engine.process_command.assert_not_called()

    def test_edit_with_session_but_no_connected_document_is_safely_blocked(self):
        result = self.parser.execute_command_result(
            "이거 고쳐줘",
            mode="edit",
            edit_context=EDIT_CONTEXT,
        )

        self.assertFalse(result["success"])
        self.assertEqual("blocked", result["status"])
        self.assertEqual("edit-session-1", result["target"])

    def test_edit_request_reaches_only_explicit_edit_handler(self):
        handler = mock.Mock(
            return_value=success_result(
                "편집 준비 완료",
                action="edit",
                target="edit-session-1",
                verified=False,
            )
        )
        self.parser.edit_mode_handler = handler

        result = self.parser.execute_command_result(
            "이 문단을 줄여줘",
            mode="edit",
            edit_context=EDIT_CONTEXT,
        )

        self.assertTrue(result["success"])
        request = handler.call_args.args[0]
        self.assertEqual("edit-session-1", request.edit_session_id)
        self.assertEqual(FINGERPRINT, request.document_fingerprint)

    def test_unknown_mode_is_blocked_instead_of_becoming_command(self):
        self.parser.analyze_command = mock.Mock()
        result = self.parser.execute_command_result("메모장 열어", mode="unknown")
        self.assertFalse(result["success"])
        self.assertEqual("mode_policy", result["action"])
        self.assertEqual("blocked", result["status"])
        self.parser.analyze_command.assert_not_called()

    def test_question_mode_cannot_execute_llm_open_app_action(self):
        self.parser.dict_mgr.noun_dict["메모장"] = "notepad.exe"
        self.parser.llm_engine.process_command = mock.Mock(
            return_value={
                "response": "메모장을 열겠습니다.",
                "action": "open_app",
                "target": "메모장",
            }
        )
        with mock.patch("engine.pipeline.conversation_route.os.startfile") as startfile:
            result = self.parser.execute_command_result(
                [{"role": "user", "content": "메모장 열어줘"}],
                mode="question",
                use_api=True,
            )
        self.assertFalse(result["success"])
        self.assertEqual("mode_policy", result["action"])
        startfile.assert_not_called()

    def test_command_api_accepts_edit_request_contract(self):
        handler = mock.Mock(
            return_value=success_result("준비 완료", action="edit", verified=False)
        )
        self.parser.edit_mode_handler = handler
        previous = command_api.parser
        command_api.parser = self.parser
        try:
            result = command_api.parse_command(
                "선택 영역을 정리해줘",
                mode="edit",
                session_id="chat-session-1",
                edit_context=EDIT_CONTEXT,
            )
        finally:
            command_api.parser = previous
        self.assertTrue(result["success"])
        self.assertEqual("edit", result["action"])
        handler.assert_called_once()


if __name__ == "__main__":
    unittest.main()
