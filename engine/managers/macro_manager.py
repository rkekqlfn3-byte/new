import threading


class MacroManager:
    def __init__(self, macro_dict, save_callback, lock=None):
        self.macro_dict = macro_dict
        self.save = save_callback
        self.lock = lock or threading.RLock()

    def add_macro_synonym(self, macro_key, synonym):
        with self.lock:
            if macro_key in self.macro_dict:
                synonyms = self.macro_dict[macro_key].setdefault("synonyms", [])
                if synonym not in synonyms:
                    synonyms.append(synonym)
                    self.save()
                    return True
            return False

    def remove_macro_synonym(self, macro_key, synonym):
        with self.lock:
            if macro_key in self.macro_dict:
                synonyms = self.macro_dict[macro_key].get("synonyms", [])
                if synonym in synonyms:
                    synonyms.remove(synonym)
                    self.save()
                    return True
            return False

    def add_custom_macro(self, macro_id, name, synonyms, macro_type, data):
        with self.lock:
            if not isinstance(synonyms, list):
                synonyms = [x.strip() for x in synonyms.split(',') if x.strip()]
            self.macro_dict[macro_id] = {
                "name": name,
                "synonyms": synonyms,
                "type": macro_type,
                "data": data,
                "description": f"[{macro_type}] {data}"
            }
            self.save()
            return True

    def delete_custom_macro(self, macro_id):
        with self.lock:
            if macro_id in self.macro_dict:
                del self.macro_dict[macro_id]
                self.save()
                return True
            return False

    def add_synonym(self, macro_name, synonym):
        with self.lock:
            if macro_name in self.macro_dict:
                if synonym not in self.macro_dict[macro_name]["synonyms"]:
                    self.macro_dict[macro_name]["synonyms"].append(synonym)
                    self.save()
                    return True
            return False
