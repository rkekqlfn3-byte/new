"""Stage 7 follow-up editing, rewrite previews, and one-level verified undo."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from engine.app_actions import PreparedAction
from engine.edit_mode.contracts import (
    EditPreparedAction,
    EditRequest,
    RiskLevel,
)
from engine.edit_mode.stage5 import Stage5EditError
from engine.edit_mode.stage6 import Stage6NativeEditAdapter
from engine.edit_mode.target_identity import (
    direct_text_selection_anchor,
    formatting_snapshot,
)
from engine.edit_mode.text_tone import classify_text_tone


UNDO_PHRASES = (
    "방금 거 취소해",
    "방금거 취소해",
    "직전 작업 취소",
    "직전 편집 취소",
    "원래대로",
    "되돌려",
    "undo",
)
FOLLOW_UP_PHRASES = (
    "조금 더",
    "좀 더",
    "너무 길어",
    "너무 많이 줄였어",
    "다시",
    "그거 말고",
    "두 번째 문장만",
    "두번째 문장만",
    "마지막 문장은 원래대로",
    "마지막 문장 원래대로",
    "아까처럼",
)
TEXT_REPLACE_OPERATIONS = frozenset(
    {"insert_text", "replace_selection", "replace_shape_text"}
)


class Stage7EditError(Stage5EditError):
    """A follow-up or undo request cannot be safely anchored."""


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_undo_request(value) -> bool:
    text = _normalized(value)
    if "문장" in text and "원래대로" in text:
        return False
    return any(phrase in text for phrase in UNDO_PHRASES)


def is_follow_up_request(value) -> bool:
    text = _normalized(value)
    if text.startswith(("조금 더", "좀 더")):
        return True
    return any(
        phrase in text
        for phrase in FOLLOW_UP_PHRASES
        if phrase != "조금 더"
    )


def _sentences(value) -> list[str]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return []
    return [
        part.strip()
        for part in re.findall(r".+?(?:[.!?。！？]+(?=\s|$)|$)", text)
        if part.strip()
    ]


def _less_shortened(original, current) -> str:
    source = re.sub(r"\s+", " ", str(original or "")).strip()
    shortened = re.sub(r"\s+", " ", str(current or "")).strip()
    if not source or not shortened or len(shortened) >= len(source):
        raise Stage7EditError("덜 축약할 직전 원문과 수정안을 확인하지 못했습니다.")
    target = min(len(source) - 1, max(len(shortened) + 1, (len(source) + len(shortened)) // 2))
    cut = source.rfind(" ", 0, target + 1)
    if cut < max(4, target // 2):
        cut = target
    candidate = source[:cut].rstrip(" ,;:") + "…"
    if candidate in {source, shortened}:
        raise Stage7EditError("서로 다른 덜 축약된 수정안을 만들지 못했습니다.")
    return candidate


def rewrite_text_candidate(before, after, attempt=1) -> str:
    """Create one bounded local alternative without calling a cloud model."""
    original = str(before or "").strip()
    proposed = str(after or "").strip()
    if not original or not proposed or original == proposed:
        raise Stage7EditError("다시 작성할 변경 전·후 텍스트가 충분하지 않습니다.")
    if len(proposed) < len(original):
        return _less_shortened(original, proposed)
    sentences = _sentences(original)
    if len(sentences) > 1:
        candidate = " ".join(sentences[:-1])
        if candidate and candidate != proposed:
            return candidate
    raise Stage7EditError(
        "로컬 규칙으로 다른 수정안을 안전하게 만들지 못했습니다. "
        "원하는 문장을 따옴표로 직접 지정해주세요."
    )


def _replacement_command(app_type, text) -> str:
    safe = str(text or "").replace("“", "").replace("”", "")
    if app_type == "powerpoint":
        return f'제목을 “{safe}”로 바꿔줘'
    return f'선택 문장을 “{safe}”로 바꿔줘'


def _text_pair(native: PreparedAction) -> tuple[str, str]:
    if native.operation not in TEXT_REPLACE_OPERATIONS:
        return "", ""
    before = native.params.get("original_text")
    after = native.params.get("text")
    if before is None or after is None:
        return "", ""
    return str(before), str(after)


class ContinuationResolver:
    """Expand short follow-ups only while the exact post-edit target is current."""

    def __init__(self, last_action=None):
        self.last_action = dict(last_action or {})

    def _require_last(self, context: Mapping[str, Any]) -> dict:
        if not self.last_action:
            raise Stage7EditError("이어서 수정할 직전 편집 기록이 없습니다.")
        expected = str(self.last_action.get("post_context_fingerprint") or "").upper()
        actual = str(context.get("context_fingerprint") or "").upper()
        if not expected or expected != actual:
            raise Stage7EditError(
                "직전 편집 후 선택 대상이 바뀌어 후속 명령을 적용하지 않았습니다."
            )
        return self.last_action

    def resolve(self, command: str, context: Mapping[str, Any]) -> tuple[str, dict]:
        if not is_follow_up_request(command):
            return str(command), {"follow_up": False}
        last = self._require_last(context)
        normalized = _normalized(command)
        app_type = str(last.get("app_type") or context.get("app_type") or "")
        operation = str(last.get("operation") or "")
        before = str(last.get("before_text") or "")
        after = str(last.get("after_text") or "")
        previous_command = str(
            last.get("resolved_command") or last.get("original_command") or ""
        ).strip()

        if "너무 길어" in normalized:
            resolved = "조금 줄여줘"
        elif "너무 많이 줄였어" in normalized:
            resolved = _replacement_command(
                app_type,
                _less_shortened(before, after),
            )
        elif "두 번째 문장만" in normalized or "두번째 문장만" in normalized:
            originals = _sentences(before)
            revisions = _sentences(after)
            if len(originals) < 2 or len(originals) != len(revisions):
                raise Stage7EditError(
                    "직전 수정안의 두 번째 문장을 독립적으로 확정하지 못했습니다."
                )
            originals[1] = revisions[1]
            resolved = _replacement_command(app_type, " ".join(originals))
        elif "마지막 문장" in normalized and "원래대로" in normalized:
            originals = _sentences(before)
            revisions = _sentences(after)
            if not originals or len(originals) != len(revisions):
                raise Stage7EditError(
                    "직전 수정안의 마지막 문장을 안전하게 복원하지 못했습니다."
                )
            revisions[-1] = originals[-1]
            resolved = _replacement_command(app_type, " ".join(revisions))
        elif normalized in {"다시", "그거 말고"}:
            if operation in TEXT_REPLACE_OPERATIONS:
                resolved = _replacement_command(
                    app_type,
                    rewrite_text_candidate(before, after),
                )
            else:
                resolved = previous_command
        elif "조금 더" in normalized or "좀 더" in normalized:
            if operation in TEXT_REPLACE_OPERATIONS:
                resolved = "조금 줄여줘"
            elif operation == "set_text_format":
                resolved = "제목을 조금 크게" if app_type == "powerpoint" else "조금 크게"
            else:
                resolved = previous_command
        else:  # 아까처럼
            resolved = previous_command
        if not resolved:
            raise Stage7EditError("후속 명령에 사용할 직전 편집 명령이 없습니다.")
        return resolved, {
            "follow_up": True,
            "follow_up_command": str(command),
            "resolved_command": resolved,
        }


class UndoManager:
    """Restore the last structured snapshot through the owning native adapter."""

    def __init__(self, native_adapter):
        self.native_adapter = native_adapter

    def undo(self, record: Mapping[str, Any]) -> dict:
        payload = dict(record or {})
        native_payload = dict(payload.get("native_prepared_action") or {})
        native = PreparedAction.from_dict(native_payload)
        if not native.reversible:
            raise Stage7EditError("직전 작업은 안전하게 되돌릴 수 없는 작업입니다.")
        restore = getattr(self.native_adapter, "undo", None)
        if not callable(restore):
            raise Stage7EditError(f"{native.app} 되돌리기 어댑터가 준비되지 않았습니다.")
        result = restore(native, payload)
        if not isinstance(result, Mapping) or not bool(result.get("verified")):
            raise Stage7EditError("직전 편집 복원 결과를 확인하지 못했습니다.")
        return dict(result)


class Stage7NativeEditAdapter(Stage6NativeEditAdapter):
    """Stage 6 adapter plus exact-target continuation and one-level undo."""

    supported_operations = Stage6NativeEditAdapter.supported_operations | frozenset(
        {"undo_last_edit"}
    )

    def __init__(
        self,
        session,
        context_manager,
        native_adapter,
        *,
        continuation_state=None,
        analyzer=None,
    ):
        super().__init__(session, context_manager, native_adapter, analyzer=analyzer)
        state = dict(continuation_state or {})
        self.last_action = dict(state.get("last_action") or {})
        self.undo_record = dict(state.get("undo_record") or {})
        self.continuation_resolver = ContinuationResolver(self.last_action)
        self.undo_manager = UndoManager(native_adapter)

    def _prepare_undo(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> EditPreparedAction:
        if not self.undo_record:
            raise Stage7EditError("되돌릴 직전 JARVIS 편집 기록이 없습니다.")
        expected = str(self.undo_record.get("post_context_fingerprint") or "").upper()
        actual = str(context.get("context_fingerprint") or "").upper()
        if not expected or expected != actual:
            raise Stage7EditError(
                "직전 편집 후 대상이 바뀌어 되돌리기를 실행하지 않았습니다."
            )
        return EditPreparedAction(
            action_id=f"edit-undo-{uuid.uuid4().hex}",
            request_id=request.request_id,
            edit_session_id=request.edit_session_id,
            app_type=self.app_type,
            operation="undo_last_edit",
            target={
                "context_target": dict(context.get("target") or {}),
                "selection_reference": context.get("selection_reference"),
                "native_target": self.undo_record.get("target"),
            },
            arguments={"undo_record": self.undo_record, "read_only": False},
            preconditions=(
                {"kind": "document_fingerprint", "value": context["document_fingerprint"]},
                {"kind": "context_fingerprint", "value": context["context_fingerprint"]},
            ),
            risk_level=RiskLevel.LOW,
            requires_approval=False,
            verification_plan={"method": "read_restored_native_snapshot"},
            rollback_plan={"strategy": "none_after_verified_undo"},
            context_fingerprint=str(context["context_fingerprint"]),
            metadata={
                "preview": {
                    "description": "직전 JARVIS 편집 되돌리기",
                    "before": self.undo_record.get("after_preview", ""),
                    "after": self.undo_record.get("before_preview", ""),
                    "target": self.undo_record.get("target"),
                    "estimated_changes": 1,
                    "noop": False,
                },
                "undo": True,
            },
        )

    def prepare(
        self,
        request: EditRequest,
        context: Mapping[str, Any],
    ) -> EditPreparedAction:
        if is_undo_request(request.text):
            return self._prepare_undo(request, context)
        resolved, follow_up = self.continuation_resolver.resolve(request.text, context)
        active_request = request
        if resolved != request.text:
            active_request = EditRequest(
                text=resolved,
                edit_session_id=request.edit_session_id,
                document_fingerprint=request.document_fingerprint,
                request_id=request.request_id,
            )
        prepared = super().prepare(active_request, context)
        native_payload = dict(prepared.arguments.get("native_prepared_action") or {})
        native = PreparedAction.from_dict(native_payload) if native_payload else None
        before_text, after_text = _text_pair(native) if native else ("", "")
        rewrite_supported = False
        if (
            native
            and native.operation in TEXT_REPLACE_OPERATIONS
            and before_text
            and after_text
            and before_text != after_text
        ):
            try:
                rewrite_text_candidate(before_text, after_text)
                rewrite_supported = True
            except Stage7EditError:
                pass
        metadata = dict(prepared.metadata)
        metadata.update({
            "stage7": {
                **follow_up,
                "original_command": request.text,
                "resolved_command": resolved,
                "before_text": before_text,
                "after_text": after_text,
            },
            "rewrite_supported": rewrite_supported,
        })
        return replace(prepared, metadata=metadata)

    def execute(self, prepared_action: EditPreparedAction) -> Mapping[str, Any]:
        if prepared_action.operation == "undo_last_edit":
            return self.undo_manager.undo(
                prepared_action.arguments.get("undo_record") or {}
            )
        return super().execute(prepared_action)

    def rollback(self, prepared_action: EditPreparedAction) -> bool:
        if prepared_action.operation == "undo_last_edit":
            return False
        return super().rollback(prepared_action)


def build_commit_records(
    request: EditRequest,
    prepared: EditPreparedAction,
    result,
    post_context: Mapping[str, Any],
    previous_state: Mapping[str, Any] | None = None,
) -> tuple[dict, dict | None]:
    """Build private JSON-only continuation and undo records after verification."""
    previous = dict((previous_state or {}).get("last_action") or {})
    native_payload = dict(prepared.arguments.get("native_prepared_action") or {})
    native = PreparedAction.from_dict(native_payload) if native_payload else None
    stage7 = dict(prepared.metadata.get("stage7") or {})
    preview = dict(prepared.metadata.get("preview") or {})
    same_chain = (
        str(previous.get("post_context_fingerprint") or "").upper()
        == str(prepared.context_fingerprint or "").upper()
    )
    sequence_count = int(previous.get("sequence_count") or 0) + 1 if same_chain else 1
    post_anchor = direct_text_selection_anchor(
        prepared.app_type,
        post_context,
    )
    post_digest = post_context.get("selected_text_digest")
    post_length = int(post_context.get("selected_text_length") or 0)
    post_tone = str(post_context.get("selected_text_tone") or "unknown")
    post_formatting = formatting_snapshot(
        prepared.app_type,
        post_context,
    )
    if (
        native
        and prepared.app_type == "hwp"
        and native.operation == "insert_text"
        and bool(native.current_state.get("has_selection"))
    ):
        coordinates = native.params.get("selection_coordinates") or []
        after_text = str(stage7.get("after_text") or "")
        if (
            isinstance(coordinates, (list, tuple))
            and len(coordinates) >= 6
            and after_text
        ):
            synthetic_target = {"coordinates": list(coordinates[:6])}
            after_result = result.observations.get("after") or {}
            after_format = (
                after_result.get("format")
                if isinstance(after_result, Mapping)
                else None
            )
            if isinstance(after_format, Mapping):
                synthetic_target.update({
                    "bold": after_format.get("bold"),
                    "font_size_hu": after_format.get("font_size_hu"),
                    "paragraph_alignment": after_format.get("alignment"),
                })
            synthetic_context = {
                "app_type": "hwp",
                "selection_kind": "text",
                "target": synthetic_target,
            }
            post_anchor = direct_text_selection_anchor(
                "hwp", synthetic_context
            )
            post_digest = hashlib.sha256(
                after_text.encode("utf-8")
            ).hexdigest().upper()
            post_length = len(after_text)
            post_tone = classify_text_tone(after_text)
            post_formatting = formatting_snapshot(
                "hwp", synthetic_context
            )
    last_action = {
        "action_id": prepared.action_id,
        "request_id": request.request_id,
        "app_type": prepared.app_type,
        "operation": prepared.operation,
        "original_command": stage7.get("original_command") or request.text,
        "resolved_command": stage7.get("resolved_command") or request.text,
        "before_text": stage7.get("before_text") or "",
        "after_text": stage7.get("after_text") or "",
        "before_preview": preview.get("before") or "",
        "after_preview": preview.get("after") or "",
        "target": preview.get("target") or prepared.target.get("native_target"),
        "selection_reference": post_context.get("selection_reference"),
        "post_document_fingerprint": post_context.get("document_fingerprint"),
        "post_selection_anchor": post_anchor,
        "post_selected_text_digest": post_digest,
        "post_selected_text_length": post_length,
        "post_selected_text_tone": post_tone,
        "post_selection_formatting": post_formatting,
        "pre_context_fingerprint": prepared.context_fingerprint,
        "post_context_fingerprint": post_context.get("context_fingerprint"),
        "sequence_count": min(sequence_count, 1000),
        "completed_at": _timestamp(),
    }
    undo_record = None
    if native and native.reversible:
        undo_record = {
            "schema_version": 1,
            "app_type": native.app,
            "operation": native.operation,
            "target": native.target,
            "document_fingerprint": post_context.get("document_fingerprint"),
            "post_context_fingerprint": post_context.get("context_fingerprint"),
            "native_prepared_action": native.to_dict(),
            "after_observations": dict(result.observations),
            "before_preview": preview.get("before") or "",
            "after_preview": preview.get("after") or "",
            "created_at": _timestamp(),
        }
        encoded = json.dumps(
            undo_record,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 500 * 1024:
            undo_record = None
    return last_action, undo_record


def rewrite_pending_command(prepared: EditPreparedAction) -> str:
    stage7 = dict(prepared.metadata.get("stage7") or {})
    before = stage7.get("before_text")
    after = stage7.get("after_text")
    candidate = rewrite_text_candidate(before, after)
    return _replacement_command(prepared.app_type, candidate)
