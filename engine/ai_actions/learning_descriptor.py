from engine.learning_schema import normalize_learning_metadata


class LearningDescriptor:
    def __init__(self, owner):
        self.owner = owner

    def describe(self, act):
        parser = self.owner
        action = act.get("action")
        app_name = str(act.get("app_name") or "시스템").strip()
        macro_name = str(act.get("macro_name") or "dynamic_code").strip()
        target = str(act.get("target") or "")
        if action == "dynamic_code":
            learning = normalize_learning_metadata(act)
            argument = parser._build_dynamic_argument(learning, target, app_name)
            code = act.get("code", "")
            label = f"새 동적 작업 {app_name}/{macro_name}"
        elif action == "adapted_macro":
            argument = act.get("_execution_argument", target)
            code = act.get("code", "")
            label = f"변형 매크로 {app_name}/{macro_name}"
        elif action == "use_learned_macro":
            learned = parser._get_learned_macro(app_name, macro_name)
            if not learned or learned.get("plan") or not learned.get("code"):
                return None
            argument = act.get("_execution_argument", target)
            code = learned.get("code", "")
            label = f"저장된 매크로 {app_name}/{macro_name}"
        else:
            return None
        return {
            "action": action,
            "app_name": app_name,
            "macro_name": macro_name,
            "target": target,
            "argument": argument,
            "code": code,
            "label": label,
        }
