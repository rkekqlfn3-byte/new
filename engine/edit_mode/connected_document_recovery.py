"""Bounded recovery service for one already-connected native document."""

from __future__ import annotations

import os

from engine.edit_mode.context import EditContextUnavailable
from engine.edit_mode.session import EditSessionStale, document_identity_fingerprint
from engine.recovery import PreExecutionRecoveryContract, recovery_target_signature


class ConnectedDocumentRecoveryService:
    """Rediscover or reopen an exact document without widening its identity."""

    def __init__(
        self,
        *,
        session_manager,
        intake_manager,
        window_activator,
        capture_context,
        find_document_window,
        record_event,
        decorate_error,
    ):
        self.session_manager = session_manager
        self.intake_manager = intake_manager
        self.window_activator = window_activator
        self.capture_context = capture_context
        self.find_document_window = find_document_window
        self.record_event = record_event
        self.decorate_error = decorate_error

    @staticmethod
    def _signature(strategy, fingerprint):
        return recovery_target_signature(strategy, [str(fingerprint or "")])

    def _begin(self, strategy, fingerprint, initial_error):
        signature = self._signature(strategy, fingerprint)
        contract = PreExecutionRecoveryContract(
            strategy=strategy,
            target_kind="document",
            target_signature=signature,
        )
        if not contract.begin(signature):
            proof = contract.to_dict()
            self.record_event(proof["outcome"], proof)
            raise self.decorate_error(initial_error, proof)
        self.record_event("retrying", contract.to_dict())
        return contract, signature

    @staticmethod
    def _fingerprint(file_path, app_type):
        try:
            return document_identity_fingerprint(file_path, app_type)
        except Exception:
            return ""

    def _raise_failure(
        self,
        contract,
        outcome,
        current_signature,
        error,
    ):
        contract.complete(
            outcome,
            current_target_signature=current_signature,
            target_resolved=False,
        )
        proof = contract.to_dict()
        self.record_event(proof["outcome"], proof)
        raise self.decorate_error(error, proof)

    def _validate_source_identity(
        self,
        contract,
        strategy,
        file_path,
        app_type,
        expected_fingerprint,
        message,
    ):
        actual = self._fingerprint(file_path, app_type)
        if actual != expected_fingerprint:
            self._raise_failure(
                contract,
                "not_found",
                self._signature(strategy, actual),
                EditSessionStale(message),
            )
        return actual

    def _capture_rebound(self, session, handle, signature):
        current = session
        if handle > 0 and handle != int(session.get("window_handle") or 0):
            try:
                current = self.session_manager.rebind_window_handle(
                    session["session_id"], handle
                )
            except Exception:
                current = session
        try:
            return self.capture_context(current)
        except Exception:
            raise

    def _complete(self, contract, strategy, current, context, request, message):
        fingerprint = (
            context.get("document_fingerprint")
            or current.get("document_fingerprint")
        )
        final_signature = self._signature(strategy, fingerprint)
        target_resolved = bool(
            current.get("session_id") == request.edit_session_id
            and current.get("document_fingerprint") == request.document_fingerprint
            and context.get("document_fingerprint") == request.document_fingerprint
        )
        outcome = contract.complete(
            "recovered" if target_resolved else "not_found",
            current_target_signature=final_signature,
            target_resolved=target_resolved,
        )
        proof = contract.to_dict()
        self.record_event(outcome, proof)
        if outcome != "recovered":
            raise self.decorate_error(EditSessionStale(message), proof)
        return current, context, proof

    def rediscover(self, session, request, initial_error, *, allow_reopen=False):
        strategy = "connected_document_rediscovery"
        contract, signature = self._begin(
            strategy, request.document_fingerprint, initial_error
        )
        app_type = str(session.get("app_type") or "").casefold()
        file_path = str(session.get("file_path") or "")
        self._validate_source_identity(
            contract,
            strategy,
            file_path,
            app_type,
            str(request.document_fingerprint or ""),
            "연결된 문서 파일이 바뀌었거나 삭제되어 편집하지 않았습니다. "
            "현재 문서를 다시 연결해주세요.",
        )
        try:
            found = self.find_document_window(app_type, file_path)
        except Exception:
            self._raise_failure(contract, "unavailable", signature, initial_error)
        if found is None:
            contract.complete(
                "not_found",
                current_target_signature=signature,
                target_resolved=False,
            )
            proof = contract.to_dict()
            self.record_event(proof["outcome"], proof)
            if allow_reopen:
                return self.reopen(session, request, initial_error)
            raise self.decorate_error(initial_error, proof)
        try:
            current, context = self._capture_rebound(
                session, int(found.get("window_handle") or 0), signature
            )
        except Exception as error:
            self._raise_failure(contract, "unavailable", signature, error)
        return self._complete(
            contract,
            strategy,
            current,
            context,
            request,
            "문서를 다시 찾는 동안 연결 대상이 달라져 편집하지 않았습니다.",
        )

    def reopen(self, session, request, initial_error):
        strategy = "connected_document_reopen"
        contract, signature = self._begin(
            strategy, request.document_fingerprint, initial_error
        )
        app_type = str(session.get("app_type") or "").casefold()
        file_path = str(session.get("file_path") or "")
        expected = str(request.document_fingerprint or "")
        self._validate_source_identity(
            contract,
            strategy,
            file_path,
            app_type,
            expected,
            "닫힌 연결 문서 파일이 바뀌었거나 삭제되어 다시 열지 않았습니다. "
            "현재 문서를 다시 연결해주세요.",
        )
        reopened = self._reopen_exact(
            contract, signature, strategy, file_path, app_type, expected
        )
        handle = self._validated_reopened_handle(
            contract, signature, strategy, reopened, file_path, app_type, expected
        )
        current, context = self._activate_and_capture(
            contract, signature, session, handle
        )
        return self._complete(
            contract,
            strategy,
            current,
            context,
            request,
            "문서를 다시 연 뒤 확인한 대상이 연결 문서와 달라 편집하지 않았습니다.",
        )

    def _reopen_exact(
        self, contract, signature, strategy, file_path, app_type, expected
    ):
        try:
            return self.intake_manager.reopen_exact_file(file_path)
        except Exception as error:
            failed = self._fingerprint(file_path, app_type)
            if failed != expected:
                self._raise_failure(
                    contract,
                    "not_found",
                    self._signature(strategy, failed),
                    EditSessionStale(
                        "문서를 다시 열기 직전에 연결 파일이 바뀌어 중단했습니다. "
                        "현재 문서를 다시 연결해주세요."
                    ),
                )
            self._raise_failure(contract, "unavailable", signature, error)

    def _validated_reopened_handle(
        self, contract, signature, strategy, reopened, file_path, app_type, expected
    ):
        try:
            reopened_path = os.path.normcase(
                os.path.abspath(str(reopened.get("file_path") or ""))
            )
            expected_path = os.path.normcase(os.path.abspath(file_path))
            reopened_app = str(reopened.get("app_type") or "").casefold()
            after = document_identity_fingerprint(reopened_path, reopened_app)
        except Exception:
            reopened_path = ""
            expected_path = os.path.normcase(os.path.abspath(file_path))
            reopened_app = ""
            after = ""
        if not (
            reopened_path == expected_path
            and reopened_app == app_type
            and after == expected
        ):
            self._raise_failure(
                contract,
                "not_found",
                self._signature(strategy, after),
                EditSessionStale(
                    "다시 열린 문서가 연결 대상과 일치하지 않아 편집하지 않았습니다. "
                    "현재 문서를 다시 연결해주세요."
                ),
            )
        handle = int(reopened.get("window_handle") or 0)
        if handle <= 0:
            self._raise_failure(
                contract,
                "unavailable",
                signature,
                EditContextUnavailable(
                    "다시 열린 연결 문서 창을 안전하게 식별하지 못했습니다."
                ),
            )
        return handle

    def _activate_and_capture(self, contract, signature, session, handle):
        try:
            current = self.session_manager.rebind_window_handle(
                session["session_id"], handle
            )
            activation = self.window_activator.activate(handle)
            if not activation.get("success") or not activation.get("focused"):
                raise EditContextUnavailable(
                    "다시 열린 연결 문서 창을 앞으로 가져오지 못했습니다."
                )
            return self.capture_context(current)
        except Exception as error:
            self._raise_failure(contract, "unavailable", signature, error)
