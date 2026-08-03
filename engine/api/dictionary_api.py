import logging
import os
import subprocess
import sys

import eel

from engine.core import get_dict_manager, get_parser

logger = logging.getLogger(__name__)

@eel.expose
def scan_apps(force=False):
    return get_dict_manager().scan_apps(force=force)

@eel.expose
def scan_recent_apps():
    return get_dict_manager().scan_recent_apps(hours=24)

@eel.expose
def scan_web_bookmarks():
    dict_mgr = get_dict_manager()
    dict_mgr.load()
    return dict_mgr.scan_web_bookmarks()

@eel.expose
def get_nouns():
    return get_dict_manager().get_nouns()

@eel.expose
def add_noun_synonym(original_noun, synonym):
    return get_dict_manager().add_noun_synonym(original_noun, synonym)

@eel.expose
def remove_noun_synonym(synonym):
    return get_dict_manager().remove_noun_synonym(synonym)

@eel.expose
def add_custom_noun(noun, path):
    return get_dict_manager().add_custom_noun(noun, path)

@eel.expose
def rename_noun(old_noun, new_noun):
    return get_dict_manager().rename_noun(old_noun, new_noun)

@eel.expose
def get_synonyms_for_path(path, exclude_noun=None):
    return get_dict_manager().get_synonyms_for_path(path, exclude_noun)

@eel.expose
def toggle_favorite(noun):
    return get_dict_manager().toggle_favorite(noun)

@eel.expose
def get_favorites():
    return get_dict_manager().get_favorites()

@eel.expose
def ai_find_exe(query):
    return get_dict_manager().ai_find_exe(query)

@eel.expose
def get_macros():
    return get_dict_manager().macro_dict

@eel.expose
def add_custom_macro(macro_id, name, synonyms, macro_type, data):
    dict_mgr = get_dict_manager()
    if macro_type not in {"hotkey", "cmd", "compound"}:
        return {"success": False, "message": "지원하지 않는 매크로 유형입니다."}
    if macro_type == "cmd":
        try:
            get_parser().builtins.validate_command(data)
        except (TypeError, ValueError) as error:
            return {"success": False, "message": str(error)}
    return dict_mgr.macro_manager.add_custom_macro(macro_id, name, synonyms, macro_type, data)

@eel.expose
def delete_custom_macro(macro_id):
    dict_mgr = get_dict_manager()
    entry = dict_mgr.macro_dict.get(macro_id, {})
    if entry.get("type") == "learned":
        deleted = dict_mgr.delete_learned_macro(entry.get("app"), macro_id)
        if deleted:
            get_parser().template_matcher.invalidate()
        return deleted
    return dict_mgr.macro_manager.delete_custom_macro(macro_id)

@eel.expose
def get_learned_macros():
    return get_dict_manager().get_learned_macro_records()

@eel.expose
def update_learned_macro(app_name, macro_name, edits):
    try:
        dict_mgr = get_dict_manager()
        result = dict_mgr.update_learned_macro(app_name, macro_name, edits)
        get_parser().template_matcher.invalidate()
        return {"success": True, "record": result}
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}

@eel.expose
def delete_learned_macro(app_name, macro_name):
    dict_mgr = get_dict_manager()
    deleted = dict_mgr.delete_learned_macro(app_name, macro_name)
    if deleted:
        get_parser().template_matcher.invalidate()
    return deleted

@eel.expose
def set_learned_macro_state(app_name, macro_name, state):
    try:
        dict_mgr = get_dict_manager()
        result = dict_mgr.set_learned_macro_state(app_name, macro_name, state)
        get_parser().template_matcher.invalidate()
        return {"success": True, "record": result}
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}

@eel.expose
def add_macro_synonym(macro_key, synonym):
    return get_dict_manager().macro_manager.add_macro_synonym(
        macro_key, synonym
    )

@eel.expose
def remove_macro_synonym(macro_key, synonym):
    return get_dict_manager().macro_manager.remove_macro_synonym(
        macro_key, synonym
    )

@eel.expose
def restart_jarvis():
    logger.info("앱 재시작 요청을 처리합니다")
    frozen = bool(getattr(sys, "frozen", False))
    command = [sys.executable] if frozen else [sys.executable, *sys.argv]
    environment = os.environ.copy()
    if frozen:
        # A PyInstaller one-file child normally reuses its parent's temporary
        # extraction directory. The parent removes that directory while
        # exiting, so an independent restart must request a fresh extraction.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(command, env=environment)
    os._exit(0)
