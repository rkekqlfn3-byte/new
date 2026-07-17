"""Prepare, execute, verify, and rollback one allowlisted edit action."""

from __future__ import annotations

from typing import Mapping

from engine.edit_mode.contracts import (
    EditContractError,
    EditExecutionResult,
    EditPreparedAction,
    EditRequest,
    validate_edit_adapter,
)
from engine.edit_mode.state_machine import EditSessionState, EditSessionStateMachine


class EditExecutionError(RuntimeError):
    error_type = "execution_error"
    status = "failed"


class EditContextChanged(EditExecutionError):
    error_type = "validation_error"
    status = "context_changed"


class EditApprovalRequired(EditExecutionError):
    error_type = "validation_error"
    status = "confirmation_required"


class EditVerificationError(EditExecutionError):
    error_type = "verification_error"


def _fingerprint(value) -> str:
    return str(value or "").strip().upper()


def _execution_error(message, error):
    """Keep machine-readable failure context while hiding it from prose parsing."""
    wrapped = EditExecutionError(f"{message}: {error}")
    failed_step = getattr(error, "failed_step", getattr(error, "step", None))
    if failed_step not in (None, ""):
        wrapped.failed_step = failed_step
    wrapped.retryable = bool(getattr(error, "retryable", False))
    context = getattr(error, "diagnostic_context", None)
    if isinstance(context, Mapping):
        wrapped.diagnostic_context = dict(context)
    return wrapped


class EditExecutionCoordinator:
    """Execute only structured operations declared by one trusted adapter."""

    def __init__(self, adapter, state_machine: EditSessionStateMachine):
        self.adapter = adapter
        self.state_machine = state_machine
        self.supported_operations = validate_edit_adapter(adapter)

    def _mark_stale(self, action_id, reason):
        self.state_machine.transition(
            EditSessionState.STALE_CONTEXT,
            reason=reason,
            action_id=action_id,
        )
        raise EditContextChanged(reason)

    def prepare(self, request: EditRequest) -> EditPreparedAction:
        if not isinstance(request, EditRequest):
            raise EditContractError("편집 준비에는 EditRequest가 필요합니다.")
        self.state_machine.require_state(EditSessionState.READY)
        self.state_machine.transition(
            EditSessionState.PREPARING,
            reason="prepare requested",
            action_id=request.request_id,
        )
        try:
            context = self.adapter.get_context()
            context_fingerprint = _fingerprint(self.adapter.fingerprint(context))
            if (
                request.context_fingerprint
                and request.context_fingerprint != context_fingerprint
            ):
                self._mark_stale(
                    request.request_id,
                    "명령 전 확인한 선택 영역이 바뀌어 편집 미리보기를 만들지 않았습니다.",
                )
            # Stage 2 adapters used one fingerprint for both document identity
            # and live context.  Stage 4+ contexts deliberately keep those
            # values separate so a selection change does not look like a
            # different file.  Preserve the legacy comparison for older and
            # test adapters that do not expose ``document_fingerprint``.
            document_fingerprint = (
                _fingerprint(context.get("document_fingerprint"))
                if isinstance(context, Mapping)
                else ""
            )
            expected_fingerprint = document_fingerprint or context_fingerprint
            if expected_fingerprint != request.document_fingerprint:
                self._mark_stale(
                    request.request_id,
                    "편집 준비 전에 문서 상태가 바뀌어 다시 연결해야 합니다.",
                )
            prepared = self.adapter.prepare(request, context)
            if isinstance(prepared, Mapping):
                prepared = EditPreparedAction.from_dict(prepared)
            if not isinstance(prepared, EditPreparedAction):
                raise EditContractError("어댑터가 구조화된 편집 작업을 반환하지 않았습니다.")
            if prepared.operation not in self.supported_operations:
                raise EditContractError(
                    f"어댑터 허용 목록에 없는 편집 작업입니다: {prepared.operation}"
                )
            if prepared.edit_session_id != request.edit_session_id:
                raise EditContractError("준비된 작업의 편집 세션이 요청과 다릅니다.")
            if prepared.request_id != request.request_id:
                raise EditContractError("준비된 작업의 요청 ID가 원래 요청과 다릅니다.")
            if prepared.app_type.casefold() != str(self.adapter.app_type).casefold():
                raise EditContractError("준비된 작업의 앱 유형이 어댑터와 다릅니다.")
            if prepared.context_fingerprint != context_fingerprint:
                raise EditContractError("준비된 작업 fingerprint가 현재 문서와 다릅니다.")
            # Round-trip validation proves there is no live COM object or code
            # object hidden in the action envelope.
            prepared = EditPreparedAction.from_dict(prepared.to_dict())
            self.state_machine.transition(
                EditSessionState.PREPARED,
                reason="structured action prepared",
                action_id=prepared.action_id,
            )
            if prepared.requires_approval:
                self.state_machine.transition(
                    EditSessionState.APPROVAL_REQUIRED,
                    reason="risk policy requires approval",
                    action_id=prepared.action_id,
                )
            return prepared
        except EditContextChanged:
            raise
        except Exception as error:
            if self.state_machine.state is EditSessionState.PREPARING:
                self.state_machine.transition(
                    EditSessionState.FAILED,
                    reason="prepare failed",
                    action_id=request.request_id,
                    error=error,
                )
            if isinstance(error, EditContractError):
                raise
            raise _execution_error("편집 작업 준비에 실패했습니다", error) from error

    def _rollback(self, prepared) -> bool:
        try:
            return bool(self.adapter.rollback(prepared))
        except Exception:
            return False

    def execute(
        self, prepared: EditPreparedAction, *, approved=False
    ) -> EditExecutionResult:
        if isinstance(prepared, Mapping):
            prepared = EditPreparedAction.from_dict(prepared)
        if not isinstance(prepared, EditPreparedAction):
            raise EditContractError("실행할 편집 작업 정보가 올바르지 않습니다.")
        expected_state = (
            EditSessionState.APPROVAL_REQUIRED
            if prepared.requires_approval
            else EditSessionState.PREPARED
        )
        self.state_machine.require_state(expected_state)
        if prepared.requires_approval and not approved:
            raise EditApprovalRequired("이 편집 작업은 사용자 승인 후 실행할 수 있습니다.")

        context = self.adapter.get_context()
        if _fingerprint(self.adapter.fingerprint(context)) != prepared.context_fingerprint:
            self._mark_stale(
                prepared.action_id,
                "준비 이후 문서 상태가 바뀌어 편집 작업을 실행하지 않았습니다.",
            )
        self.state_machine.transition(
            EditSessionState.EXECUTING,
            reason="adapter execution started",
            action_id=prepared.action_id,
        )
        try:
            raw_result = self.adapter.execute(prepared)
            if not isinstance(raw_result, Mapping):
                raise EditContractError("어댑터 실행 결과는 직렬화 가능한 객체여야 합니다.")
            provisional_result = EditExecutionResult.from_value(
                prepared.action_id, raw_result, verified=False
            )
        except Exception as error:
            rolled_back = self._rollback(prepared)
            self.state_machine.transition(
                EditSessionState.ROLLED_BACK if rolled_back else EditSessionState.FAILED,
                reason="execution failed",
                action_id=prepared.action_id,
                error=error,
            )
            raise _execution_error("편집 실행에 실패했습니다", error) from error

        self.state_machine.transition(
            EditSessionState.VERIFYING,
            reason="read-back verification started",
            action_id=prepared.action_id,
        )
        try:
            verified = bool(
                self.adapter.verify(prepared, provisional_result.observations)
            )
        except Exception:
            verified = False
        if not verified:
            rolled_back = self._rollback(prepared)
            self.state_machine.transition(
                EditSessionState.ROLLED_BACK if rolled_back else EditSessionState.FAILED,
                reason="verification failed",
                action_id=prepared.action_id,
            )
            verification_error = EditVerificationError(
                "편집 결과 검증에 실패해 변경을 복구했거나 작업을 중단했습니다."
            )
            verification_error.failed_step = prepared.operation
            verification_error.retryable = False
            raise verification_error
        result = EditExecutionResult(
            action_id=prepared.action_id,
            changed=provisional_result.changed,
            verified=True,
            observations=provisional_result.observations,
        )
        self.state_machine.transition(
            EditSessionState.COMMITTED,
            reason="verified edit committed",
            action_id=prepared.action_id,
        )
        return result
