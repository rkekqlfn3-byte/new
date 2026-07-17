from engine.confirmation.handlers.action_plan import resolve as resolve_action_plan
from engine.confirmation.handlers.app_method import resolve as resolve_app_method
from engine.confirmation.handlers.app_target import resolve as resolve_app_target
from engine.confirmation.handlers.basic import resolve_command_macro, resolve_demo
from engine.confirmation.handlers.dynamic_code import resolve as resolve_dynamic_code
from engine.confirmation.handlers.edit_action import resolve as resolve_edit_action
from engine.confirmation.handlers.hwp_scope import resolve as resolve_hwp_scope
from engine.confirmation.handlers.prepared_action import resolve as resolve_prepared_action
from engine.confirmation.handlers.skill_policy import resolve as resolve_skill_policy
from engine.confirmation.handlers.uia_target import resolve as resolve_uia_target

HANDLERS = {
    "demo": resolve_demo,
    "command_macro": resolve_command_macro,
    "dynamic_code_preflight": resolve_dynamic_code,
    "skill_run_policy": resolve_skill_policy,
    "uia_target_choice": resolve_uia_target,
    "hwp_scope_choice": resolve_hwp_scope,
    "app_target_choice": resolve_app_target,
    "app_method_choice": resolve_app_method,
    "prepared_app_action": resolve_prepared_action,
    "prepared_edit_action": resolve_edit_action,
    "action_plan_overwrite": resolve_action_plan,
}
