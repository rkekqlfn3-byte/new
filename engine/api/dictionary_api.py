import eel
from engine.core import dict_mgr, parser

@eel.expose
def scan_apps(force=False):
    return dict_mgr.scan_apps(force=force)

@eel.expose
def scan_recent_apps():
    return dict_mgr.scan_recent_apps(hours=24)

@eel.expose
def scan_web_bookmarks():
    dict_mgr.load()
    return dict_mgr.scan_web_bookmarks()

@eel.expose
def get_nouns():
    return dict_mgr.get_nouns()

@eel.expose
def add_noun_synonym(original_noun, synonym):
    return dict_mgr.add_noun_synonym(original_noun, synonym)

@eel.expose
def remove_noun_synonym(synonym):
    return dict_mgr.remove_noun_synonym(synonym)

@eel.expose
def add_custom_noun(noun, path):
    return dict_mgr.add_custom_noun(noun, path)

@eel.expose
def rename_noun(old_noun, new_noun):
    return dict_mgr.rename_noun(old_noun, new_noun)

@eel.expose
def get_synonyms_for_path(path, exclude_noun=None):
    return dict_mgr.get_synonyms_for_path(path, exclude_noun)

@eel.expose
def toggle_favorite(noun):
    return dict_mgr.toggle_favorite(noun)

@eel.expose
def get_favorites():
    return dict_mgr.get_favorites()

@eel.expose
def ai_find_exe(query):
    return dict_mgr.ai_find_exe(query)

@eel.expose
def get_macros():
    return dict_mgr.macro_dict

@eel.expose
def add_custom_macro(macro_id, name, synonyms, macro_type, data):
    if macro_type not in {"hotkey", "cmd", "compound"}:
        return {"success": False, "message": "지원하지 않는 매크로 유형입니다."}
    if macro_type == "cmd":
        try:
            parser.builtins.validate_command(data)
        except (TypeError, ValueError) as error:
            return {"success": False, "message": str(error)}
    return dict_mgr.macro_manager.add_custom_macro(macro_id, name, synonyms, macro_type, data)

@eel.expose
def delete_custom_macro(macro_id):
    entry = dict_mgr.macro_dict.get(macro_id, {})
    if entry.get("type") == "learned":
        deleted = dict_mgr.delete_learned_macro(entry.get("app"), macro_id)
        if deleted:
            parser.template_matcher.invalidate()
        return deleted
    return dict_mgr.macro_manager.delete_custom_macro(macro_id)

@eel.expose
def get_learned_macros():
    return dict_mgr.get_learned_macro_records()

@eel.expose
def update_learned_macro(app_name, macro_name, edits):
    try:
        result = dict_mgr.update_learned_macro(app_name, macro_name, edits)
        parser.template_matcher.invalidate()
        return {"success": True, "record": result}
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}

@eel.expose
def delete_learned_macro(app_name, macro_name):
    deleted = dict_mgr.delete_learned_macro(app_name, macro_name)
    if deleted:
        parser.template_matcher.invalidate()
    return deleted

@eel.expose
def set_learned_macro_state(app_name, macro_name, state):
    try:
        result = dict_mgr.set_learned_macro_state(app_name, macro_name, state)
        parser.template_matcher.invalidate()
        return {"success": True, "record": result}
    except (TypeError, ValueError) as error:
        return {"success": False, "message": str(error)}

@eel.expose
def add_macro_synonym(macro_key, synonym):
    return dict_mgr.macro_manager.add_macro_synonym(macro_key, synonym)

@eel.expose
def remove_macro_synonym(macro_key, synonym):
    return dict_mgr.macro_manager.remove_macro_synonym(macro_key, synonym)

@eel.expose
def restart_jarvis():
    import sys
    import os
    import subprocess
    print("[System] 앱 재시작. 전체 프로세스를 종료하고 다시 시작합니다...")
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable] + sys.argv
    subprocess.Popen(command)
    os._exit(0)
