import eel
import os
from engine.core import dict_mgr
from engine.runtime_paths import user_data_path
from engine.storage.json_store import (
    atomic_write_json,
    get_recovery_events,
    safe_read_json,
)

@eel.expose
def get_ai_config():
    return dict_mgr.config_manager.get_ai_config()

@eel.expose
def save_ai_config(provider, api_key, ollama_model="llama3", routing_mode="auto"):
    return dict_mgr.config_manager.save_ai_config(
        provider, api_key, ollama_model, routing_mode
    )

@eel.expose
def get_storage_recovery_events(clear=True):
    return get_recovery_events(clear=bool(clear))

USER_MEMORY_PATH = user_data_path("user_memory.json")

@eel.expose
def save_user_memory(mem_user, mem_rules, mem_others):
    data = {
        "user_info": mem_user,
        "rules": mem_rules,
        "others": mem_others
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
                "others": data.get("others", "")
            }
        except (OSError, AttributeError):
            pass
    return {"user_info": "", "rules": "", "others": ""}

CHAT_SESSION_DIR = user_data_path("sessions")

@eel.expose
def get_chat_sessions():
    sessions = []
    if not os.path.exists(CHAT_SESSION_DIR):
        return sessions
    for f in os.listdir(CHAT_SESSION_DIR):
        if f.endswith('.json'):
            path = os.path.join(CHAT_SESSION_DIR, f)
            try:
                data = safe_read_json(path, {})
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                sessions.append({
                    "id": data.get("id"),
                    "title": data.get("title", "새로운 대화"),
                    "timestamp": data.get("timestamp", 0)
                })
            except (OSError, AttributeError, TypeError):
                pass
    sessions.sort(key=lambda x: x["timestamp"], reverse=True)
    return sessions

@eel.expose
def load_chat_session(session_id):
    path = os.path.join(CHAT_SESSION_DIR, f"{session_id}.json")
    if os.path.exists(path):
        try:
            data = safe_read_json(path, None)
            return data if isinstance(data, dict) else None
        except Exception as e:
            print(f"Error loading session: {e}")
    return None

@eel.expose
def save_chat_session(session_id, title, messages, summary="", state=None, **kwargs):
    os.makedirs(CHAT_SESSION_DIR, exist_ok=True)
    path = os.path.join(CHAT_SESSION_DIR, f"{session_id}.json")
    
    # Load existing to preserve timestamp if it exists
    existing_data = {}
    if os.path.exists(path):
        loaded = safe_read_json(path, {})
        if isinstance(loaded, dict):
            existing_data = loaded
        
    import time
    data = {
        "id": session_id,
        "title": title,
        "timestamp": existing_data.get("timestamp", int(time.time() * 1000)),
        "messages": messages,
        "summary": summary,
        "state": state or {}
    }
    atomic_write_json(path, data)
    return True

@eel.expose
def delete_chat_session(session_id):
    path = os.path.join(CHAT_SESSION_DIR, f"{session_id}.json")
    if os.path.exists(path):
        try:
            os.remove(path)
            backup_path = f"{path}.bak"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            return True
        except:
            return False
    return False
