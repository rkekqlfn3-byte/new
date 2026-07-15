"""Single source of truth for Jarvis action-plan capabilities."""

TRANSIENT_ERRORS = (
    "execution_error", "verification_error", "target_not_found",
    "environment_error",
)


def _policy(max_retries=0, error_types=(), **extra):
    return {
        **extra,
        "retryable": max_retries > 0,
        "max_retries": int(max_retries),
        "retry_error_types": tuple(error_types),
    }


ACTION_SPECS = {
    # Launching, clicking, typing, navigation, and file writes can duplicate an
    # external side effect. They are retried only by an explicit new command.
    "open_app": _policy(app_target=True),
    "focus_window": _policy(1, TRANSIENT_ERRORS, app_target=True),
    "move_window": _policy(1, TRANSIENT_ERRORS, app_target=True),
    "window_state": _policy(1, TRANSIENT_ERRORS, app_target=True),
    "hotkey": _policy(),
    "type_text": _policy(),
    "wait": _policy(),
    "navigate_url": _policy(),
    # Clipboard set and these UIA operations are idempotent for the same input.
    "clipboard_set": _policy(1, ("execution_error", "environment_error"), required=("text",)),
    "clipboard_get": _policy(1, ("execution_error", "environment_error")),
    "copy_file": _policy(required=("target", "text")),
    "move_file": _policy(required=("target", "text")),
    "create_folder": _policy(1, ("execution_error", "environment_error"), required=("target",)),
    "write_text_file": _policy(required=("target", "text")),
    # Native app commands are executed only with a PreparedAction approved by
    # the parser-side decision flow. ``params_template`` is the stored form and
    # is rendered to ``params`` before preparation. Writes are never retried.
    "app_command": _policy(
        required=("target", "operation", "params"), native_action=True,
    ),
    # direction remains the legacy name-only selector. Stage 9 plans can use
    # the structured selector object instead, so only the app target is common.
    "uia_click": _policy(required=("target",), app_target=True),
    "uia_set_text": _policy(1, TRANSIENT_ERRORS, required=("target", "text"), app_target=True),
    "uia_select_file": _policy(1, TRANSIENT_ERRORS, required=("target",)),
}

ALLOWED_ACTIONS = frozenset(ACTION_SPECS)


def action_spec(action):
    return ACTION_SPECS.get(action, {})


def app_target_actions():
    return {name for name, spec in ACTION_SPECS.items() if spec.get("app_target")}


def retry_limit(action, error_type, override=None):
    spec = action_spec(action)
    if error_type not in spec.get("retry_error_types", ()):
        return 0
    configured = max(0, min(int(spec.get("max_retries", 0) or 0), 2))
    if override is None:
        return configured
    return min(configured, max(0, min(int(override or 0), 2)))
