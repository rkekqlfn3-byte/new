"""Grouped confirmation services without a persistent parser back-reference."""

import copy

from engine.confirmation.confirmation_dispatcher import ConfirmationDispatcher
from engine.confirmation.confirmation_factory import ConfirmationFactory
from engine.confirmation.confirmation_response_handler import (
    ConfirmationResponseHandler,
)
from engine.managers.pending_confirmation_manager import (
    PendingConfirmationManager,
    normalize_session_id,
)


class ConfirmationRegistry:
    """Own creation, storage, response parsing, and resume dispatch.

    ``runtime`` is supplied only while an operation is being performed.  None
    of the child services retains the ``CommandParser`` that owns this group.
    """

    def __init__(
        self,
        execution_controller,
        pending_manager=None,
        handlers=None,
    ):
        self.pending = pending_manager or PendingConfirmationManager()
        self.responses = ConfirmationResponseHandler(
            self.pending, execution_controller
        )
        self.factory = ConfirmationFactory(self.pending, self.responses)
        self.dispatcher = ConfirmationDispatcher(self.responses, handlers)

    def get_pending(self, session_id=None):
        return self.responses.get_pending(session_id)

    def result(self, record, message=None):
        return self.responses.confirmation_result(record, message)

    def resolve(self, runtime, *args, **kwargs):
        return self.dispatcher.resolve(runtime, *args, **kwargs)

    def queue_missing_information(self, runtime, *args, **kwargs):
        return self.factory.queue_missing_information(runtime, *args, **kwargs)

    def queue_command_macro(self, runtime, *args, **kwargs):
        return self.factory.queue_command_macro(runtime, *args, **kwargs)

    def queue_app_method(self, runtime, *args, **kwargs):
        return self.factory.queue_app_method(runtime, *args, **kwargs)

    def queue_hwp_scope(self, runtime, *args, **kwargs):
        return self.factory.queue_hwp_scope(runtime, *args, **kwargs)

    def queue_app_target(self, runtime, *args, **kwargs):
        return self.factory.queue_app_target(runtime, *args, **kwargs)

    def queue_dynamic_code(self, runtime, *args, **kwargs):
        return self.factory.queue_dynamic_code(runtime, *args, **kwargs)

    def queue_local_learned_dynamic(self, runtime, *args, **kwargs):
        return self.factory.queue_local_learned_dynamic(
            runtime, *args, **kwargs
        )

    def queue_skill_run_policy(self, runtime, *args, **kwargs):
        return self.factory.queue_skill_run_policy(runtime, *args, **kwargs)

    def queue_uia_target(self, runtime, *args, **kwargs):
        return self.factory.queue_uia_target(runtime, *args, **kwargs)

    def queue_learned_uia_target(self, runtime, *args, **kwargs):
        return self.factory.queue_learned_uia_target(
            runtime, *args, **kwargs
        )

    def _execution_id(self):
        current = self.responses.execution_controller.current
        return current.get("execution_id", "") if isinstance(current, dict) else ""

    def queue_demo(self, original_command, session_id):
        record = self.pending.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._execution_id(),
            original_command=original_command,
            reason="confirmation_demo",
            message=(
                "확인 카드 테스트를 계속할까요? 다른 파일이나 프로그램은 "
                "변경하지 않습니다."
            ),
            action="confirmation_demo",
            options=[
                {
                    "id": "continue",
                    "label": "계속",
                    "description": "아무 작업도 변경하지 않고 확인 흐름만 완료합니다.",
                    "recommended": True,
                    "aliases": ["네", "예", "응", "오케이", "진행", "계속해"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "테스트를 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마"],
                },
            ],
            payload={"kind": "demo"},
        )
        return self.result(record)

    def queue_prepared_action(
        self,
        prepared,
        request,
        decision,
        session_id,
        original_command,
        execution_id="",
        preference_selection=None,
        continuation=None,
    ):
        record = self.pending.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._execution_id() or str(execution_id or ""),
            original_command=original_command,
            reason=decision.reason or "destructive_action",
            message=decision.message or "준비된 작업을 실행할까요?",
            action="app_command",
            target=f"{prepared.workbook_name}/{prepared.sheet}/{prepared.target}",
            options=decision.options,
            payload={
                "kind": "prepared_app_action",
                "request": copy.deepcopy(request),
                "prepared_action": prepared.to_dict(),
                "preference_selection": copy.deepcopy(preference_selection),
                "continuation": copy.deepcopy(continuation),
            },
        )
        return self.result(record)

    def queue_action_plan(
        self, runtime, error, payload, session_id, original_command
    ):
        record = self.pending.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._execution_id() or payload.get("execution_id", ""),
            original_command=original_command,
            reason="destructive_action",
            message=f"같은 이름의 파일이 있습니다. 덮어쓸까요?\n{error.target}",
            action=error.action,
            target=error.target,
            options=[
                {
                    "id": "overwrite",
                    "label": "덮어쓰기",
                    "description": "기존 파일을 새 내용으로 교체합니다.",
                    "danger": True,
                    "aliases": ["네", "예", "응", "덮어써", "교체", "진행"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "원본 파일을 유지하고 작업을 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "하지마"],
                },
            ],
            payload={
                **copy.deepcopy(payload),
                "kind": "action_plan_overwrite",
                "confirmation_action": error.action,
                "confirmation_target": error.target,
            },
        )
        return self.result(record)

    def queue_pdf_external_action(self, prepared, session_id, original_command):
        """Pause before any PDF text crosses the configured AI boundary."""
        transfer = dict(prepared.get("transfer") or {})
        pages = tuple(int(page) for page in transfer.get("page_numbers", ()))
        page_label = ", ".join(str(page) for page in pages)
        provider = str(transfer.get("provider", "AI")).upper()
        message = (
            f"PDF 내용을 {provider}로 전송해 분석할까요?\n"
            f"- 전송 페이지: {page_label} ({len(pages)}페이지)\n"
            f"- 전송 글자 수: {int(transfer.get('character_count', 0)):,}자\n"
            "- 원본 PDF는 변경하지 않습니다."
        )
        output_names = list((prepared.get("office") or {}).get("output_names") or ())
        if output_names:
            message += "\n- 새로 만들 파일: " + ", ".join(map(str, output_names))
        record = self.pending.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._execution_id(),
            original_command=original_command,
            reason="pdf_external_transfer",
            message=message,
            action=f"pdf_{prepared.get('intent', 'analysis')}",
            target=prepared.get("connection_id"),
            options=[
                {
                    "id": "continue",
                    "label": "전송하고 실행",
                    "description": "표시된 페이지만 AI로 전송해 분석합니다.",
                    "recommended": True,
                    "aliases": ["응", "네", "예", "진행", "계속", "실행"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "PDF 내용을 전송하지 않고 작업을 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "취소"],
                },
            ],
            payload=copy.deepcopy(prepared),
        )
        return self.result(record)

    def queue_pdf_office_action(self, prepared, session_id, original_command):
        """Pause before creating an Office artifact from a verified PDF table."""
        office = dict(prepared.get("office") or {})
        output_names = list(office.get("output_names") or ())
        message = (
            "확인한 PDF 표로 새 Excel 파일을 만들까요?\n"
            f"- 새로 만들 파일: {', '.join(map(str, output_names))}\n"
            "- 원본 PDF는 변경하지 않으며 기존 파일을 덮어쓰지 않습니다."
        )
        record = self.pending.create(
            session_id=normalize_session_id(session_id),
            execution_id=self._execution_id(),
            original_command=original_command,
            reason="pdf_office_create",
            message=message,
            action="pdf_table_to_excel",
            target=prepared.get("connection_id"),
            options=[
                {
                    "id": "continue",
                    "label": "파일 만들기",
                    "description": "표를 새 Excel 파일에 쓰고 다시 읽어 검증합니다.",
                    "recommended": True,
                    "aliases": ["응", "네", "예", "진행", "계속", "실행"],
                },
                {
                    "id": "cancel",
                    "label": "취소",
                    "description": "파일을 만들지 않고 취소합니다.",
                    "cancel": True,
                    "aliases": ["아니", "아니요", "그만", "취소"],
                },
            ],
            payload=copy.deepcopy(prepared),
        )
        return self.result(record)
