from engine.ai_actions.basic_action_executor import (
    execute_open_app,
    execute_read_and_analyze,
)
from engine.ai_actions.generated_action_executor import (
    execute_action_plan,
    execute_dynamic_code,
)
from engine.ai_actions.learned_action_executor import (
    execute_adapted_action,
    execute_learned_action,
)

_HANDLERS = {
    "open_app": execute_open_app,
    "read_and_analyze": execute_read_and_analyze,
    "use_learned_macro": execute_learned_action,
    "adapted_macro": execute_adapted_action,
    "action_plan": execute_action_plan,
    "dynamic_code": execute_dynamic_code,
}


def dispatch_batch_action(context, action):
    handler = _HANDLERS.get(action.get("action", "none"))
    return handler(context, action) if handler else None
