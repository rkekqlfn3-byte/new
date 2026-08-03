import hashlib
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from engine.app_actions import PreparedAction
from engine.edit_mode.contracts import EditPreparedAction, EditRequest, RiskLevel
from engine.edit_mode.stage7 import (
    ContinuationResolver,
    Stage7EditError,
    Stage7NativeEditAdapter,
    UndoManager,
    build_commit_records,
    is_follow_up_request,
    is_undo_request,
    rewrite_pending_command,
    rewrite_text_candidate,
)

FP_A = "A" * 64
FP_B = "B" * 64


def native_action(**overrides):
    values = {
        "app": "word",
        "operation": "replace_selection",
        "document_id": "C:/fixture/stage7.docx",
        "workbook_name": "stage7.docx",
        "sheet": "현재 문서",
        "target": "선택 텍스트",
        "params": {
            "text": "짧게 정리한 문장입니다.",
            "original_text": "이 문장은 조금 더 길고 자세한 원문입니다.",
        },
        "current_state": {"value": None},
        "estimated_changes": 1,
        "destructive": True,
        "reversible": True,
        "verification_method": "read_back",
        "context_fingerprint": FP_A,
        "prepared_at": "2026-07-16T12:00:00+09:00",
    }
    values.update(overrides)
    return PreparedAction(**values)


def edit_action(native=None, **metadata):
    native = native or native_action()
    return EditPreparedAction(
        action_id="edit-stage7-action",
        request_id="stage7-request",
        edit_session_id="stage7-session",
        app_type=native.app,
        operation=native.operation,
        target={"native_target": native.target},
        arguments={"native_prepared_action": native.to_dict()},
        preconditions=({"kind": "context_fingerprint", "value": FP_A},),
        risk_level=RiskLevel.MEDIUM,
        requires_approval=True,
        verification_plan={"method": "read_back"},
        rollback_plan={"strategy": "snapshot"},
        context_fingerprint=FP_A,
        metadata={
            "preview": {
                "before": native.params.get("original_text", ""),
                "after": native.params.get("text", ""),
                "target": native.target,
            },
            "stage7": {
                "original_command": "조금 줄여줘",
                "resolved_command": "조금 줄여줘",
                "before_text": native.params.get("original_text", ""),
                "after_text": native.params.get("text", ""),
            },
            **metadata,
        },
    )


class Stage7EditingTests(unittest.TestCase):
    @staticmethod
    def _undo_adapter(*, action_id="edit-action", created_at=None):
        return Stage7NativeEditAdapter(
            {"app_type": "word"},
            SimpleNamespace(),
            SimpleNamespace(),
            continuation_state={
                "last_action": {"action_id": action_id},
                "undo_record": {
                    "target": "selection",
                    "post_context_fingerprint": FP_A,
                    "created_at": created_at or datetime.now().astimezone().isoformat(),
                },
            },
        )

    def test_undo_button_token_must_match_latest_action(self):
        adapter = self._undo_adapter(action_id="latest-action")
        request = EditRequest(
            text="방금 작업 되돌려줘",
            edit_session_id="stage7-session",
            document_fingerprint=FP_B,
            request_id="stage7-undo-request",
            context_fingerprint=FP_A,
            expected_undo_action_id="older-action",
        )
        with self.assertRaises(Stage7EditError):
            adapter._prepare_undo(request, {"context_fingerprint": FP_A})

    def test_undo_record_expires_after_five_minutes(self):
        created = (
            datetime.now().astimezone() - timedelta(minutes=6)
        ).isoformat()
        adapter = self._undo_adapter(created_at=created)
        request = EditRequest(
            text="방금 작업 되돌려줘",
            edit_session_id="stage7-session",
            document_fingerprint=FP_B,
            request_id="stage7-expired-undo",
            context_fingerprint=FP_A,
            expected_undo_action_id="edit-action",
        )
        with self.assertRaises(Stage7EditError):
            adapter._prepare_undo(request, {"context_fingerprint": FP_A})
    def test_follow_up_and_undo_phrases_do_not_overlap_partial_restore(self):
        self.assertTrue(is_follow_up_request("조금 더"))
        self.assertTrue(is_follow_up_request("조금 더 줄여줘"))
        self.assertFalse(is_follow_up_request("제목을 조금 더 크게"))
        self.assertTrue(is_follow_up_request("마지막 문장은 원래대로"))
        self.assertFalse(is_undo_request("마지막 문장은 원래대로"))
        self.assertTrue(is_undo_request("방금 거 취소해"))
        self.assertTrue(is_undo_request("원래대로"))

    def test_continuation_requires_the_exact_post_edit_context(self):
        resolver = ContinuationResolver({
            "post_context_fingerprint": FP_A,
            "resolved_command": "조금 줄여줘",
            "operation": "replace_selection",
        })
        with self.assertRaises(Stage7EditError):
            resolver.resolve("아까처럼", {"context_fingerprint": FP_B})

    def test_second_sentence_only_and_last_sentence_restore_are_structured(self):
        resolver = ContinuationResolver({
            "post_context_fingerprint": FP_A,
            "app_type": "word",
            "operation": "replace_selection",
            "before_text": "원문 하나. 원문 둘.",
            "after_text": "수정 하나. 수정 둘.",
            "resolved_command": "선택 문장을 바꿔줘",
        })
        second, _ = resolver.resolve(
            "두 번째 문장만",
            {"context_fingerprint": FP_A},
        )
        restored, _ = resolver.resolve(
            "마지막 문장은 원래대로",
            {"context_fingerprint": FP_A},
        )
        self.assertIn("원문 하나. 수정 둘.", second)
        self.assertIn("수정 하나. 원문 둘.", restored)

    def test_local_rewrite_is_distinct_and_bounded_by_the_original(self):
        original = "이 문장은 중요한 배경과 근거를 함께 설명하는 긴 원문입니다."
        shortened = "중요한 근거를 설명합니다."
        candidate = rewrite_text_candidate(original, shortened)
        self.assertNotEqual(shortened, candidate)
        self.assertNotEqual(original, candidate)
        self.assertGreater(len(candidate), len(shortened))
        self.assertLessEqual(len(candidate), len(original))
        prepared = edit_action(native_action(params={
            "text": shortened,
            "original_text": original,
        }))
        self.assertIn(candidate, rewrite_pending_command(prepared))

    def test_undo_manager_delegates_only_to_a_verified_native_restore(self):
        class Native:
            def __init__(self):
                self.calls = 0

            def undo(self, prepared, record):
                self.calls += 1
                return {"verified": True, "changed": True}

        native = Native()
        manager = UndoManager(native)
        result = manager.undo({"native_prepared_action": native_action().to_dict()})
        self.assertTrue(result["verified"])
        self.assertEqual(1, native.calls)

    def test_commit_records_increment_only_on_the_same_exact_context(self):
        request = EditRequest(
            text="조금 줄여줘",
            edit_session_id="stage7-session",
            document_fingerprint=FP_B,
            request_id="stage7-request",
        )
        prepared = edit_action()
        result = SimpleNamespace(observations={"verified": True, "changed": True})
        first, undo = build_commit_records(
            request,
            prepared,
            result,
            {
                "app_type": "word",
                "context_fingerprint": FP_B,
                "document_fingerprint": FP_B,
                "selection_reference": "10:110",
                "selection_kind": "text",
                "target": {"start": 10, "end": 110},
            },
        )
        second, _ = build_commit_records(
            request,
            EditPreparedAction.from_dict({
                **prepared.to_dict(),
                "context_fingerprint": FP_B,
            }),
            result,
            {"context_fingerprint": "C" * 64, "document_fingerprint": FP_B},
            {"last_action": first},
        )
        self.assertEqual(1, first["sequence_count"])
        self.assertEqual(2, second["sequence_count"])
        self.assertEqual(FP_B, first["post_document_fingerprint"])
        self.assertEqual(
            {
                "schema_version": 1,
                "kind": "word_text",
                "start": 10,
            },
            first["post_selection_anchor"],
        )
        self.assertEqual("replace_selection", undo["operation"])

    def test_hwp_replacement_keeps_verified_start_text_and_format_after_cursor_collapse(self):
        replacement = "한글 교체 결과 문장입니다."
        native = native_action(
            app="hwp",
            operation="insert_text",
            document_id="C:/fixture/stage7.hwp",
            workbook_name="stage7.hwp",
            params={
                "text": replacement,
                "original_text": "기존 한글 문장입니다.",
                "selection_coordinates": [0, 1, 10, 0, 1, 30],
            },
            current_state={
                "has_selection": True,
                "selected_length": 20,
                "selected_digest": "D" * 64,
            },
        )
        prepared = edit_action(native)
        result = SimpleNamespace(observations={
            "verified": True,
            "changed": True,
            "after": {
                "format": {
                    "bold": 0,
                    "font_size_hu": 1100,
                    "alignment": 1,
                },
            },
        })
        record, _ = build_commit_records(
            EditRequest(
                text='선택 문장을 "한글 교체 결과 문장입니다."으로 바꿔줘',
                edit_session_id="stage7-session",
                document_fingerprint=FP_B,
                request_id="stage7-request-hwp",
            ),
            prepared,
            result,
            {
                "app_type": "hwp",
                "context_fingerprint": FP_B,
                "document_fingerprint": FP_B,
                "selection_reference": "cursor:0:1:24",
                "selection_kind": "cursor",
                "selected_text_digest": hashlib.sha256(b"").hexdigest().upper(),
                "selected_text_length": 0,
                "target": {"position": [0, 1, 24]},
            },
        )

        self.assertEqual(
            {
                "schema_version": 1,
                "kind": "hwp_text",
                "start": [0, 1, 10],
            },
            record["post_selection_anchor"],
        )
        self.assertEqual(len(replacement), record["post_selected_text_length"])
        self.assertEqual(
            hashlib.sha256(replacement.encode("utf-8")).hexdigest().upper(),
            record["post_selected_text_digest"],
        )
        self.assertEqual(
            {
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
            record["post_selection_formatting"],
        )
        self.assertEqual(
            "verified_native_replacement",
            record["post_identity_source"],
        )

    def test_word_replacement_keeps_verified_range_when_com_selection_collapses(self):
        replacement = "Word 교체 결과 문장입니다."
        native = native_action(
            app="word",
            operation="replace_selection",
            params={
                "text": replacement,
                "original_text": "기존 Word 문장입니다.",
                "start": 10,
                "end": 28,
            },
            current_state={
                "has_selection": True,
                "selected_length": 18,
                "selected_digest": "D" * 64,
                "in_table": False,
                "table": {},
            },
        )
        prepared = edit_action(native)
        result = SimpleNamespace(observations={
            "verified": True,
            "changed": True,
            "format": {
                "bold": 0,
                "font_size": 11.0,
                "alignment": 0,
            },
        })
        record, _ = build_commit_records(
            EditRequest(
                text='선택 문장을 "Word 교체 결과 문장입니다."으로 바꿔줘',
                edit_session_id="stage7-session",
                document_fingerprint=FP_B,
                request_id="stage7-request-word",
            ),
            prepared,
            result,
            {
                "app_type": "word",
                "context_fingerprint": FP_B,
                "document_fingerprint": FP_B,
                "selection_reference": "10:10",
                "selection_kind": "cursor",
                "selected_text_digest": hashlib.sha256(b"").hexdigest().upper(),
                "selected_text_length": 0,
                "target": {"start": 10, "end": 10},
            },
        )

        self.assertEqual(
            {
                "schema_version": 1,
                "kind": "word_text",
                "start": 10,
            },
            record["post_selection_anchor"],
        )
        self.assertEqual(len(replacement), record["post_selected_text_length"])
        self.assertEqual(
            hashlib.sha256(replacement.encode("utf-8")).hexdigest().upper(),
            record["post_selected_text_digest"],
        )
        self.assertEqual(
            {
                "schema_version": 1,
                "bold": False,
                "font_size": 11.0,
                "alignment": "left",
            },
            record["post_selection_formatting"],
        )
        self.assertEqual(
            "verified_native_replacement",
            record["post_identity_source"],
        )


if __name__ == "__main__":
    unittest.main()
