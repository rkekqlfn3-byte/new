"""In-memory, session-scoped confirmation requests.

Confirmation state is intentionally not persisted. A process restart invalidates
every pending request so stale document or application state can never be
approved after the context that produced it has disappeared.
"""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from datetime import datetime

from engine.vocabulary.confirmation import CANCEL_ALIASES

DEFAULT_CONFIRMATION_TTL_SECONDS = 300
DEFAULT_SESSION_ID = "default"
OPTION_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,64}$")
REQUEST_KINDS = frozenset({"confirmation", "clarification"})
CLARIFICATION_REASON_CODES = frozenset({
    "missing_target",
    "missing_range",
    "missing_destination",
    "missing_sort_key",
    "missing_filter_condition",
    "multiple_possible_intents",
    "ambiguous_reference",
    "multiple_documents",
    "multiple_sheets",
    "context_mismatch",
    "unsafe_default",
})


class PendingConfirmationError(ValueError):
    status = "confirmation_error"


class ConfirmationConflictError(PendingConfirmationError):
    status = "confirmation_conflict"


class ConfirmationNotFoundError(PendingConfirmationError):
    status = "confirmation_not_found"


class ConfirmationExpiredError(PendingConfirmationError):
    status = "confirmation_expired"


class ConfirmationAlreadyConsumedError(PendingConfirmationError):
    status = "confirmation_already_consumed"


class ConfirmationSessionMismatchError(PendingConfirmationError):
    status = "confirmation_session_mismatch"


class ConfirmationOptionError(PendingConfirmationError):
    status = "confirmation_invalid_option"


def normalize_session_id(value):
    text = str(value or "").strip()
    return text[:120] or DEFAULT_SESSION_ID


def normalize_answer(value):
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


class PendingConfirmationManager:
    def __init__(
        self,
        ttl_seconds=DEFAULT_CONFIRMATION_TTL_SECONDS,
        max_history=100,
        clock=None,
    ):
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.max_history = max(10, int(max_history))
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._records = {}
        self._active_by_session = {}
        self._history = []
        self._expired_execution_ids = []

    @staticmethod
    def _iso(timestamp):
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(
            timespec="seconds"
        )

    @staticmethod
    def _normalize_options(options):
        if not isinstance(options, list) or not options:
            raise ConfirmationOptionError("확인 선택지가 비어 있습니다.")
        normalized = []
        seen = set()
        for raw in options:
            if not isinstance(raw, dict):
                raise ConfirmationOptionError("확인 선택지는 객체 형식이어야 합니다.")
            option_id = str(raw.get("id", "")).strip()
            if not OPTION_ID_RE.fullmatch(option_id) or option_id in seen:
                raise ConfirmationOptionError(
                    f"확인 선택지 ID가 올바르지 않습니다: {option_id!r}"
                )
            label = str(raw.get("label", "")).strip()[:80]
            if not label:
                raise ConfirmationOptionError("확인 선택지 이름이 비어 있습니다.")
            aliases = {
                normalize_answer(option_id),
                normalize_answer(label),
            }
            for alias in raw.get("aliases", []) if isinstance(raw.get("aliases"), list) else []:
                cleaned = normalize_answer(alias)
                if cleaned:
                    aliases.add(cleaned)
            if bool(raw.get("cancel", False)):
                # Every way to say no reaches every cancel option, so an option
                # author cannot leave one out. Declining is never the dangerous
                # direction, and the lists here had already drifted apart.
                aliases.update(
                    cleaned
                    for cleaned in (
                        normalize_answer(alias) for alias in CANCEL_ALIASES
                    )
                    if cleaned
                )
            normalized.append({
                "id": option_id,
                "label": label,
                "description": str(raw.get("description", "")).strip()[:300],
                "recommended": bool(raw.get("recommended", False)),
                "danger": bool(raw.get("danger", False)),
                "cancel": bool(raw.get("cancel", False)),
                "input_only": bool(raw.get("input_only", False)),
                "aliases": sorted(aliases),
            })
            seen.add(option_id)
        return normalized

    def _remember_history_locked(self, confirmation_id):
        self._history.append(confirmation_id)
        while len(self._history) > self.max_history:
            oldest = self._history.pop(0)
            record = self._records.get(oldest)
            if record and record.get("status") != "pending":
                self._records.pop(oldest, None)

    def _expire_locked(self):
        now = self._clock()
        for session_id, confirmation_id in list(self._active_by_session.items()):
            record = self._records.get(confirmation_id)
            if not record:
                self._active_by_session.pop(session_id, None)
                continue
            if record["expires_epoch"] > now:
                continue
            record["status"] = "expired"
            record["consumed_at"] = self._iso(now)
            self._active_by_session.pop(session_id, None)
            if record.get("execution_id"):
                self._expired_execution_ids.append(record["execution_id"])
            self._remember_history_locked(confirmation_id)

    def create(
        self,
        *,
        session_id,
        execution_id,
        original_command,
        reason,
        message,
        options,
        action="confirmation",
        target=None,
        payload=None,
        rememberable=False,
        ttl_seconds=None,
        request_kind="confirmation",
    ):
        session_id = normalize_session_id(session_id)
        request_kind = str(request_kind or "confirmation").strip().lower()
        if request_kind not in REQUEST_KINDS:
            raise PendingConfirmationError("지원하지 않는 사용자 입력 요청 종류입니다.")
        if (
            request_kind == "clarification"
            and str(reason or "") not in CLARIFICATION_REASON_CODES
        ):
            raise PendingConfirmationError("지원하지 않는 정보 보완 사유 코드입니다.")
        normalized_options = self._normalize_options(options)
        if (
            request_kind == "clarification"
            and any(item.get("danger") for item in normalized_options)
        ):
            raise PendingConfirmationError(
                "정보 보완 선택지는 실행 승인이나 위험 선택지가 될 수 없습니다."
            )
        with self._lock:
            self._expire_locked()
            if session_id in self._active_by_session:
                raise ConfirmationConflictError(
                    "이 대화에는 이미 응답을 기다리는 확인 요청이 있습니다."
                )
            now = self._clock()
            ttl = self.ttl_seconds if ttl_seconds is None else max(1.0, float(ttl_seconds))
            confirmation_id = f"confirm_{uuid.uuid4().hex}"
            record = {
                "confirmation_id": confirmation_id,
                "execution_id": str(execution_id or ""),
                "session_id": session_id,
                "original_command": str(original_command or "")[:1000],
                "reason": str(reason or "confirmation_required")[:80],
                "request_kind": request_kind,
                "message": str(message or "확인이 필요합니다.")[:1000],
                "action": str(action or "confirmation")[:80],
                "target": None if target is None else str(target)[:1000],
                "rememberable": bool(rememberable),
                "options": normalized_options,
                "payload": copy.deepcopy(payload) if isinstance(payload, dict) else {},
                "created_epoch": now,
                "expires_epoch": now + ttl,
                "created_at": self._iso(now),
                "expires_at": self._iso(now + ttl),
                "consumed_at": None,
                "selected_option": None,
                "status": "pending",
            }
            self._records[confirmation_id] = record
            self._active_by_session[session_id] = confirmation_id
            return copy.deepcopy(record)

    @staticmethod
    def public_record(record):
        if not isinstance(record, dict):
            return None
        public = {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key not in {"payload", "created_epoch", "expires_epoch"}
        }
        public["options"] = [
            {key: copy.deepcopy(value) for key, value in option.items() if key != "aliases"}
            for option in record.get("options", [])
        ]
        return public

    def active_record(self, session_id, public=False):
        session_id = normalize_session_id(session_id)
        with self._lock:
            self._expire_locked()
            confirmation_id = self._active_by_session.get(session_id)
            record = self._records.get(confirmation_id) if confirmation_id else None
            if not record:
                return None
            copied = copy.deepcopy(record)
            return self.public_record(copied) if public else copied

    def get_record(self, confirmation_id, public=False):
        with self._lock:
            self._expire_locked()
            record = self._records.get(str(confirmation_id or ""))
            if not record:
                return None
            copied = copy.deepcopy(record)
            return self.public_record(copied) if public else copied

    def resolve_text(self, session_id, answer):
        normalized = normalize_answer(answer)
        if not normalized:
            return None
        record = self.active_record(session_id)
        if not record:
            return None
        matches = [
            option["id"] for option in record["options"]
            if normalized in option.get("aliases", [])
        ]
        return matches[0] if len(matches) == 1 else None

    def consume(self, session_id, confirmation_id, option_id):
        session_id = normalize_session_id(session_id)
        confirmation_id = str(confirmation_id or "").strip()
        option_id = str(option_id or "").strip()
        with self._lock:
            self._expire_locked()
            record = self._records.get(confirmation_id)
            if not record:
                raise ConfirmationNotFoundError("확인 요청을 찾을 수 없습니다.")
            if record["session_id"] != session_id:
                raise ConfirmationSessionMismatchError(
                    "다른 대화의 확인 요청에는 응답할 수 없습니다."
                )
            if record["status"] == "expired":
                raise ConfirmationExpiredError("확인 요청이 만료되었습니다.")
            if record["status"] != "pending":
                raise ConfirmationAlreadyConsumedError(
                    "이미 처리된 확인 요청입니다. 중복 실행하지 않았습니다."
                )
            option = next(
                (item for item in record["options"] if item["id"] == option_id),
                None,
            )
            if option is None:
                raise ConfirmationOptionError("선택할 수 없는 확인 응답입니다.")
            now = self._clock()
            record["status"] = "cancelled" if option.get("cancel") else "consumed"
            record["consumed_at"] = self._iso(now)
            record["selected_option"] = option_id
            self._active_by_session.pop(session_id, None)
            self._remember_history_locked(confirmation_id)
            return copy.deepcopy(record)

    def cancel(self, session_id, confirmation_id=None):
        record = self.active_record(session_id)
        if not record:
            raise ConfirmationNotFoundError("취소할 확인 요청이 없습니다.")
        if confirmation_id and record["confirmation_id"] != confirmation_id:
            raise ConfirmationNotFoundError("취소할 확인 요청을 찾을 수 없습니다.")
        cancel_option = next(
            (item for item in record["options"] if item.get("cancel")),
            None,
        )
        if not cancel_option:
            raise ConfirmationOptionError("이 확인 요청에는 취소 선택지가 없습니다.")
        return self.consume(
            session_id, record["confirmation_id"], cancel_option["id"]
        )

    def pending_count(self):
        with self._lock:
            self._expire_locked()
            return len(self._active_by_session)

    def drain_expired_execution_ids(self):
        with self._lock:
            self._expire_locked()
            expired = list(dict.fromkeys(self._expired_execution_ids))
            self._expired_execution_ids.clear()
            return expired
