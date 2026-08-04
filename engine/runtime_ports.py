"""Least-authority runtime views for domain handlers.

The composition root owns many collaborators, but a confirmation handler only
receives the names required for its continuation kind.  Values are copied into
the short-lived view, so the view does not retain the full runtime container.
"""

from __future__ import annotations

from collections.abc import Iterable


class RuntimePortView:
    """Read-only, explicit subset of runtime collaborators."""

    __slots__ = ("_values",)

    def __init__(self, runtime, names: Iterable[str]):
        values = {}
        for name in names:
            values[name] = getattr(runtime, name)
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as error:
            raise AttributeError(
                f"Runtime port does not grant access to {name!r}."
            ) from error

    def __setattr__(self, name, value):
        raise AttributeError("Runtime ports are read-only.")

    @property
    def granted_names(self):
        return frozenset(self._values)


_CONFIRMATION_PORTS = {
    "action_plan_overwrite": {
        "_authorize_plan_overwrite", "_failure_type_for_error",
        "action_executor", "confirmations", "skill_learning_service",
    },
    "app_method_choice": {
        "app_command_router", "confirmations", "decision_engine",
        "skill_executor",
    },
    "app_target_choice": {
        "_handle_format_method_request", "app_command_router",
        "confirmations", "decision_engine", "skill_executor",
    },
    "clarification_rephrase": {
        "app_command_router", "execute_command_result",
    },
    "command_macro": {"builtins"},
    "demo": set(),
    "dynamic_code_preflight": {
        "_execute_ai_action_batch", "_resume_local_learned_dynamic",
    },
    "hwp_scope_choice": {
        "app_command_router", "confirmations", "decision_engine",
        "skill_executor",
    },
    "prepared_app_action": {
        "app_command_router", "confirmations", "decision_engine",
        "skill_executor",
    },
    "prepared_edit_action": {"edit_mode_controller"},
    "prepared_pdf_external_action": {"pdf_task_service"},
    "prepared_pdf_file_action": {"pdf_task_service"},
    "prepared_pdf_file_undo": {"pdf_task_service"},
    "prepared_pdf_office_action": {"pdf_task_service"},
    "skill_run_policy": {
        "_dynamic_preflight_failure", "_execute_ai_action_batch",
        "_failure_type_for_error", "_get_learned_macro", "confirmations",
        "dict_mgr", "execution_controller", "skill_executor",
    },
    "uia_target_choice": {
        "_failure_type_for_error", "action_executor", "confirmations",
        "execution_controller", "skill_executor", "skill_learning_service",
    },
}


def confirmation_runtime_port(runtime, continuation_kind: str) -> RuntimePortView:
    """Return only the dependencies granted to one confirmation kind."""
    try:
        names = _CONFIRMATION_PORTS[continuation_kind]
    except KeyError as error:
        raise ValueError(
            f"Unknown confirmation continuation kind: {continuation_kind}"
        ) from error
    return RuntimePortView(runtime, names)


def confirmation_port_names(continuation_kind: str) -> frozenset[str]:
    """Expose immutable grants for architecture tests and review tooling."""
    try:
        return frozenset(_CONFIRMATION_PORTS[continuation_kind])
    except KeyError as error:
        raise ValueError(
            f"Unknown confirmation continuation kind: {continuation_kind}"
        ) from error


def confirmation_port_kinds() -> frozenset[str]:
    """Return every continuation kind with an explicit dependency grant."""
    return frozenset(_CONFIRMATION_PORTS)
