import unittest

from engine.user_feedback import (
    UserFeedbackEvent,
    event_from_execution_result,
    event_from_runtime_event,
    render_user_event,
)


class UserFeedbackTests(unittest.TestCase):
    def test_event_drops_paths_content_and_unknown_details(self):
        event = UserFeedbackEvent(
            event_type="action_started",
            target={
                "sheet": "매출",
                "range": "A1:B3",
                "path": r"C:\Users\person\secret.xlsx",
                "content": "민감한 셀 값",
            },
            details={
                "status": "running",
                "count": 3,
                "path": r"C:\secret.docx",
                "message": "사용자 원문",
            },
        ).to_dict()

        self.assertEqual({"sheet": "매출", "range": "A1:B3"}, event["target"])
        self.assertEqual({"status": "running", "count": 3}, event["details"])
        self.assertNotIn("secret", str(event))
        self.assertNotIn("민감한 셀 값", str(event))
        self.assertNotIn("사용자 원문", str(event))

    def test_verified_completion_and_undo_are_stated_only_when_true(self):
        verified = render_user_event(UserFeedbackEvent(
            event_type="action_completed",
            action="edit",
            success=True,
            verified=True,
            undo_available=True,
        ))
        unverified = render_user_event(UserFeedbackEvent(
            event_type="action_completed",
            action="edit",
            success=True,
            verified=False,
            undo_available=False,
        ))

        self.assertIn("결과까지 확인", verified["headline"])
        self.assertIn("되돌릴 수", verified["next_action"])
        self.assertIn("확인이 필요", unverified["headline"])
        self.assertNotIn("되돌", unverified["next_action"])

    def test_result_adapter_keeps_success_separate_from_verification(self):
        completed = event_from_execution_result({
            "success": True,
            "message": "원문 결과는 복사하지 않음",
            "action": "command",
            "verified": False,
            "status": "success",
        }, execution_id="run-1")
        verified = event_from_execution_result({
            "success": True,
            "message": "완료",
            "action": "edit",
            "verified": True,
            "status": "success",
            "data": {"undo_available": True},
        }, execution_id="run-2")

        self.assertEqual("action_completed", completed["event_type"])
        self.assertFalse(completed["verified"])
        self.assertNotIn("원문 결과", str(completed))
        self.assertEqual("verification_passed", verified["event_type"])
        self.assertTrue(verified["undo_available"])

    def test_confirmation_failure_busy_and_cancel_have_distinct_events(self):
        cases = (
            ({"success": False, "status": "confirmation_required"}, "confirmation_required"),
            ({"success": False, "status": "busy", "error_type": "busy"}, "action_busy"),
            ({"success": False, "status": "cancelled", "error_type": "user_cancelled"}, "action_cancelled"),
            ({"success": False, "status": "failed", "error_type": "timeout"}, "action_failed"),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    expected,
                    event_from_execution_result(value)["event_type"],
                )

    def test_runtime_adapter_maps_recovery_and_drops_diagnostic_content(self):
        event = event_from_runtime_event({
            "execution_id": "run-3",
            "action": "automatic_recovery",
            "status": "retrying",
            "details": {
                "attempt": 1,
                "max_attempts": 1,
                "path": r"C:\private.xlsx",
                "raw_text": "셀 내용",
            },
        })

        self.assertEqual("recovery_started", event["event_type"])
        self.assertEqual(1, event["details"]["attempt"])
        self.assertNotIn("private", str(event))
        self.assertNotIn("셀 내용", str(event))

    def test_runtime_recovery_started_is_not_generic_progress(self):
        event = event_from_runtime_event({
            "execution_id": "run-recovery",
            "action": "automatic_recovery",
            "status": "started",
            "details": {"attempt": 1, "max_attempts": 1},
        })
        self.assertEqual("recovery_started", event["event_type"])

    def test_unknown_runtime_status_is_not_exposed(self):
        self.assertIsNone(event_from_runtime_event({
            "action": "internal",
            "status": "contains_private_payload",
            "details": {"message": "비밀"},
        }))


if __name__ == "__main__":
    unittest.main()
