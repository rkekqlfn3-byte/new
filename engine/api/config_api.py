"""Eel configuration and chat-session persistence endpoints."""

import logging
import os
import re
from pathlib import Path

import eel

from engine.core import get_dict_manager
from engine.runtime_paths import user_data_path
from engine.version import runtime_info
from engine.storage.json_store import (
    atomic_write_json,
    get_recovery_events,
    safe_read_json,
)


logger = logging.getLogger(__name__)


@eel.expose
def get_ai_config():
    return get_dict_manager().config_manager.get_public_ai_config()


@eel.expose
def save_ai_config(provider, api_key, routing_mode="auto"):
    try:
        return get_dict_manager().config_manager.save_ai_config(
            provider, api_key, routing_mode
        )
    except (OSError, TypeError, ValueError):
        logger.exception(
            "AI configuration save failed",
            extra={"route": "config", "error_type": "storage_error"},
        )
        return False


@eel.expose
def clear_ai_api_key(confirmed=False):
    if confirmed is not True:
        return False
    try:
        return get_dict_manager().config_manager.clear_ai_api_key()
    except (OSError, TypeError, ValueError):
        logger.exception(
            "AI API key deletion failed",
            extra={"route": "config", "error_type": "storage_error"},
        )
        return False


@eel.expose
def get_build_info():
    return runtime_info()


@eel.expose
def get_storage_recovery_events(clear=True):
    return get_recovery_events(clear=bool(clear))


USER_MEMORY_PATH = user_data_path("user_memory.json")


@eel.expose
def save_user_memory(mem_user, mem_rules, mem_others):
    data = {
        "user_info": mem_user,
        "rules": mem_rules,
        "others": mem_others,
    }
    atomic_write_json(USER_MEMORY_PATH, data)
    return True


@eel.expose
def load_user_memory():
    if os.path.exists(USER_MEMORY_PATH):
        try:
            data = safe_read_json(USER_MEMORY_PATH, {})
            return {
                "user_info": data.get("user_info", data.get("userInfo", "")),
                "rules": data.get("rules", ""),
                "others": data.get("others", ""),
            }
        except (OSError, AttributeError):
            pass
    return {"user_info": "", "rules": "", "others": ""}


CHAT_SESSION_DIR = user_data_path("sessions")
SESSION_ID_RE = re.compile(r"^session_[A-Za-z0-9_-]{1,80}$")


def resolve_session_path(session_id: str) -> Path:
    """Return the only allowed JSON path for a chat-session identifier.

    All session file operations go through this allowlist.  Invalid input is
    rejected before any filesystem query, so traversal and drive-qualified
    paths cannot disclose or modify files outside the session directory.
    """
    if not isinstance(session_id, str):
        raise ValueError("Session ID must be a string.")
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("Invalid session ID.")

    base = Path(CHAT_SESSION_DIR).resolve()
    path = (base / f"{session_id}.json").resolve()
    if path.parent != base:
        raise ValueError("Session path escapes the session directory.")
    return path


def _valid_session_path_or_none(session_id):
    try:
        return resolve_session_path(session_id)
    except (TypeError, ValueError):
        return None


@eel.expose
def get_chat_sessions():
    sessions = []
    base = Path(CHAT_SESSION_DIR).resolve()
    if not base.is_dir():
        return sessions

    for candidate in base.glob("*.json"):
        session_id = candidate.stem
        path = _valid_session_path_or_none(session_id)
        if path is None or path != candidate.resolve():
            continue
        try:
            data = safe_read_json(path, {})
            # A file whose embedded ID disagrees with its name is malformed.
            # Never use its content to derive a path or expose it as a valid
            # session entry.
            if not isinstance(data, dict) or data.get("id") != session_id:
                continue
            timestamp = data.get("timestamp", 0)
            if not isinstance(timestamp, (int, float)):
                timestamp = 0
            sessions.append({
                "id": session_id,
                "title": str(data.get("title") or "New chat"),
                "timestamp": timestamp,
            })
        except (OSError, AttributeError, TypeError, ValueError):
            continue

    sessions.sort(key=lambda item: item["timestamp"], reverse=True)
    return sessions


@eel.expose
def load_chat_session(session_id):
    path = _valid_session_path_or_none(session_id)
    if path is None or not path.is_file():
        return None
    try:
        data = safe_read_json(path, None)
        if not isinstance(data, dict) or data.get("id") != session_id:
            return None
        return data
    except (OSError, AttributeError, TypeError, ValueError):
        return None


@eel.expose
def save_chat_session(session_id, title, messages, summary="", state=None, **kwargs):
    path = _valid_session_path_or_none(session_id)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_data = {}
    if path.is_file():
        loaded = safe_read_json(path, {})
        if isinstance(loaded, dict) and loaded.get("id") == session_id:
            existing_data = loaded

    import time

    data = {
        "id": session_id,
        "title": str(title or ""),
        "timestamp": existing_data.get("timestamp", int(time.time() * 1000)),
        "messages": messages if isinstance(messages, list) else [],
        "summary": str(summary or ""),
        "state": state if isinstance(state, dict) else {},
    }
    try:
        atomic_write_json(path, data)
    except OSError:
        return False
    return True


@eel.expose
def delete_chat_session(session_id):
    path = _valid_session_path_or_none(session_id)
    if path is None or not path.is_file():
        return False
    backup_path = Path(f"{path}.bak")
    if backup_path.parent != path.parent:
        return False
    try:
        path.unlink()
        if backup_path.is_file():
            backup_path.unlink()
        return True
    except OSError:
        return False
