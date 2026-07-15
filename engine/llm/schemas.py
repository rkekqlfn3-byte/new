import json


def decode_command_content(content):
    """Decode normal JSON and one common double-encoded provider response."""
    current = content
    for _ in range(2):
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except json.JSONDecodeError:
                return {"response": current, "action": "none", "target": None}
        if not isinstance(current, dict):
            return {"response": str(current), "action": "none", "target": None}
        if (
            not isinstance(current.get("actions"), list)
            and isinstance(current.get("response"), str)
            and current["response"].lstrip().startswith("{")
        ):
            current = current["response"]
            continue
        return current
    return current if isinstance(current, dict) else {
        "response": str(current), "action": "none", "target": None
    }


def attach_command_candidates(result, command_context):
    if not isinstance(result, dict) or command_context is None:
        return result
    result["_allowed_app_candidates"] = list(command_context.allowed_apps)
    result["_allowed_learned_candidates"] = [
        [app_name, macro_name]
        for app_name, macro_name in command_context.allowed_macros
    ]
    return result
