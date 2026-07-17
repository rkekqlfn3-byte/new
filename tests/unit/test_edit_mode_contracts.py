import unittest

from engine.edit_mode import (
    ActionScope,
    EditApprovalRequired,
    EditContextChanged,
    EditContractError,
    EditExecutionCoordinator,
    EditExecutionError,
    EditPreparedAction,
    EditRequest,
    EditSessionState,
    EditSessionStateMachine,
    EditStateConflict,
    EditStateTransitionError,
    EditVerificationError,
    ModePermissionError,
    RiskLevel,
    assert_action_allowed,
)


FINGERPRINT = "A" * 64
CHANGED_FINGERPRINT = "B" * 64


def ready_machine():
    machine = EditSessionStateMachine()
    machine.transition(EditSessionState.ATTACHING, reason="test attach")
    machine.transition(EditSessionState.READY, reason="test ready")
    return machine


class FakeEditAdapter:
    app_type = "word"
    supported_operations = frozenset({"replace_text"})

    def __init__(self, *, requires_approval=False, verify=True):
        self.context = {"fingerprint": FINGERPRINT}
        self.requires_approval = requires_approval
        self.verify_result = verify
        self.executed = 0
        self.rolled_back = 0
        self.raw_result = None

    def get_context(self):
        return dict(self.context)

    def fingerprint(self, context):
        return context["fingerprint"]

    def prepare(self, request, context):
        return EditPreparedAction(
            action_id=f"action-{request.request_id}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type=self.app_type,
            operation="replace_text",
            target={"kind": "range", "start": 0, "end": 4},
            arguments={"text": "after"},
            preconditions=({"kind": "text_digest", "value": "before"},),
            risk_level=RiskLevel.MEDIUM if self.requires_approval else RiskLevel.LOW,
            requires_approval=self.requires_approval,
            verification_plan={"method": "read_back"},
            rollback_plan={"strategy": "range_snapshot"},
            context_fingerprint=context["fingerprint"],
        )

    def execute(self, prepared_action):
        self.executed += 1
        if self.raw_result is not None:
            return self.raw_result
        return {"changed": True, "text": prepared_action.arguments["text"]}

    def verify(self, prepared_action, result):
        return self.verify_result and result.get("text") == "after"

    def rollback(self, prepared_action):
        self.rolled_back += 1
        return True


class EditModeContractTests(unittest.TestCase):
    def request(self):
        return EditRequest(
            text="이 문장을 바꿔줘",
            edit_session_id="edit-session-1",
            document_fingerprint=FINGERPRINT,
        )

    def test_structured_action_runs_prepare_execute_verify_commit(self):
        adapter = FakeEditAdapter()
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)

        prepared = coordinator.prepare(self.request())
        self.assertEqual(EditSessionState.PREPARED, machine.state)
        result = coordinator.execute(prepared)

        self.assertTrue(result.changed)
        self.assertTrue(result.verified)
        self.assertEqual(1, adapter.executed)
        self.assertEqual(EditSessionState.COMMITTED, machine.state)

    def test_approval_is_required_before_medium_risk_execution(self):
        adapter = FakeEditAdapter(requires_approval=True)
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)
        prepared = coordinator.prepare(self.request())

        self.assertEqual(EditSessionState.APPROVAL_REQUIRED, machine.state)
        with self.assertRaises(EditApprovalRequired):
            coordinator.execute(prepared)
        self.assertEqual(0, adapter.executed)
        result = coordinator.execute(prepared, approved=True)
        self.assertTrue(result.verified)

    def test_changed_fingerprint_blocks_execution_before_write(self):
        adapter = FakeEditAdapter()
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)
        prepared = coordinator.prepare(self.request())
        adapter.context["fingerprint"] = CHANGED_FINGERPRINT

        with self.assertRaises(EditContextChanged):
            coordinator.execute(prepared)

        self.assertEqual(0, adapter.executed)
        self.assertEqual(EditSessionState.STALE_CONTEXT, machine.state)

    def test_request_context_change_blocks_before_preview_prepare(self):
        adapter = FakeEditAdapter()
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)
        request = EditRequest(
            text="이 문장을 바꿔줘",
            edit_session_id="edit-session-1",
            document_fingerprint=FINGERPRINT,
            context_fingerprint=CHANGED_FINGERPRINT,
        )

        with self.assertRaises(EditContextChanged):
            coordinator.prepare(request)

        self.assertEqual(0, adapter.executed)
        self.assertEqual(EditSessionState.STALE_CONTEXT, machine.state)

    def test_failed_verification_rolls_back(self):
        adapter = FakeEditAdapter(verify=False)
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)
        prepared = coordinator.prepare(self.request())

        with self.assertRaises(EditVerificationError):
            coordinator.execute(prepared)

        self.assertEqual(1, adapter.rolled_back)
        self.assertEqual(EditSessionState.ROLLED_BACK, machine.state)

    def test_prepared_action_rejects_non_json_arguments(self):
        with self.assertRaises(EditContractError):
            EditPreparedAction(
                action_id="action-1",
                request_id="request-1",
                edit_session_id="session-1",
                app_type="excel",
                operation="write_cell",
                target={"cell": "A1"},
                arguments={"com_object": object()},
                preconditions=(),
                risk_level="low",
                requires_approval=False,
                verification_plan={"method": "read_back"},
                rollback_plan={"strategy": "cell_snapshot"},
                context_fingerprint=FINGERPRINT,
            )

    def test_execution_result_rejects_non_json_values_and_rolls_back(self):
        adapter = FakeEditAdapter()
        adapter.raw_result = {"changed": True, "com_object": object()}
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)
        prepared = coordinator.prepare(self.request())

        with self.assertRaises(EditExecutionError):
            coordinator.execute(prepared)

        self.assertEqual(1, adapter.rolled_back)
        self.assertEqual(EditSessionState.ROLLED_BACK, machine.state)

    def test_adapter_allowlist_blocks_unknown_operation(self):
        adapter = FakeEditAdapter()
        original_prepare = adapter.prepare

        def unsupported(request, context):
            prepared = original_prepare(request, context).to_dict()
            prepared["operation"] = "run_python"
            return prepared

        adapter.prepare = unsupported
        machine = ready_machine()
        coordinator = EditExecutionCoordinator(adapter, machine)

        with self.assertRaises(EditContractError):
            coordinator.prepare(self.request())

        self.assertEqual(0, adapter.executed)
        self.assertEqual(EditSessionState.FAILED, machine.state)

    def test_high_risk_action_cannot_disable_approval(self):
        with self.assertRaises(EditContractError):
            EditPreparedAction(
                action_id="action-1",
                request_id="request-1",
                edit_session_id="session-1",
                app_type="excel",
                operation="delete_sheet",
                target={"sheet": "Sheet1"},
                arguments={},
                preconditions=(),
                risk_level="high",
                requires_approval=False,
                verification_plan={"method": "sheet_absent"},
                rollback_plan={"strategy": "workbook_backup"},
                context_fingerprint=FINGERPRINT,
            )

    def test_state_machine_rejects_illegal_and_stale_revision_transitions(self):
        machine = EditSessionStateMachine()
        with self.assertRaises(EditStateTransitionError):
            machine.transition(EditSessionState.EXECUTING)
        machine.transition(EditSessionState.ATTACHING)
        revision = machine.revision
        machine.transition(EditSessionState.READY, expected_revision=revision)
        with self.assertRaises(EditStateConflict):
            machine.transition(
                EditSessionState.PREPARING,
                expected_revision=revision,
            )

    def test_permission_matrix_blocks_question_write_and_edit_global_action(self):
        with self.assertRaises(ModePermissionError):
            assert_action_allowed("question", ActionScope.DOCUMENT_WRITE)
        with self.assertRaises(ModePermissionError):
            assert_action_allowed("edit", ActionScope.GLOBAL_ACTION)
        assert_action_allowed("edit", ActionScope.DOCUMENT_WRITE)
        assert_action_allowed("command", ActionScope.GLOBAL_ACTION)


if __name__ == "__main__":
    unittest.main()
