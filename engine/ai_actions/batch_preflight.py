from engine.security import BLOCKED, CONFIRMATION_REQUIRED


class BatchPreflight:
    def __init__(self, learning_descriptor):
        self.learning_descriptor = learning_descriptor

    def run(
        self,
        parser,
        response,
        actions,
        validation_issues,
        user_input_str,
        session_id,
        log_callback=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        image_data=None,
        use_api=False,
    ):
        approved = set(approved_fingerprints or [])
        approved_skills = {
            str(value).casefold() for value in (approved_skill_runs or [])
        }
        for act in actions:
            if act.get("action") != "use_learned_macro":
                continue
            app_name = str(act.get("app_name") or "시스템").strip()
            macro_name = str(act.get("macro_name") or "").strip()
            key = f"{app_name}/{macro_name}".casefold()
            if key in approved_skills:
                continue
            learned = parser._get_learned_macro(app_name, macro_name)
            if not isinstance(learned, dict):
                continue
            assessment = parser.skill_run_policy.assess(
                learned, user_input_str
            )
            if assessment.preview_only:
                return parser._skill_policy_preview_result(
                    app_name, macro_name, learned, assessment
                )
            if assessment.route == "python":
                decision = parser.skill_executor.preflight(
                    app_name,
                    macro_name,
                    skill=learned,
                    argument=str(act.get("target") or ""),
                    action="use_learned_macro",
                    target=str(act.get("target") or ""),
                    log_callback=log_callback,
                )
                if decision.status == BLOCKED:
                    return parser._dynamic_preflight_failure(
                        decision.result,
                        action="use_learned_macro",
                        target=macro_name,
                    )
                if decision.status == CONFIRMATION_REQUIRED:
                    if decision.fingerprint in approved:
                        approved_skills.add(key)
                    # The common dynamic-code gate below provides the required
                    # one-shot confirmation for this Python skill.
                    continue
            if assessment.requires_confirmation:
                return parser.confirmations.queue_skill_run_policy(
                    parser,
                    app_name=app_name,
                    macro_name=macro_name,
                    skill=learned,
                    assessment=assessment,
                    session_id=session_id,
                    original_command=user_input_str,
                    resume_payload={
                        "resume_mode": "ai_batch",
                        "response": response,
                        "actions": actions,
                        "validation_issues": validation_issues,
                        "user_input": user_input_str,
                        "image_data": image_data,
                        "use_api": bool(use_api),
                        "approval_fingerprints": sorted(approved),
                        "approved_skill_runs": sorted(approved_skills),
                    },
                )
        confirmation_items = []
        for act in actions:
            descriptor = self.learning_descriptor.describe(parser, act)
            if not descriptor:
                continue
            if descriptor["action"] == "dynamic_code":
                result, fingerprint = parser._analyze_dynamic_code(
                    descriptor["code"],
                    descriptor["argument"],
                    action=descriptor["action"],
                    app_name=descriptor["app_name"],
                    macro_name=descriptor["macro_name"],
                    target=descriptor["target"],
                    log_callback=log_callback,
                )
            else:
                decision = parser.skill_executor.preflight(
                    descriptor["app_name"],
                    descriptor["macro_name"],
                    code_override=(
                        descriptor["code"]
                        if descriptor["action"] == "adapted_macro"
                        else None
                    ),
                    argument=descriptor["argument"],
                    action=descriptor["action"],
                    target=descriptor["target"],
                    log_callback=log_callback,
                )
                result = decision.result
                fingerprint = decision.fingerprint
            if result.status == BLOCKED:
                return parser._dynamic_preflight_failure(
                    result,
                    action=descriptor["action"],
                    target=descriptor["macro_name"],
                )
            if (
                result.status == CONFIRMATION_REQUIRED
                and fingerprint not in approved
            ):
                confirmation_items.append({
                    "label": descriptor["label"],
                    "fingerprint": fingerprint,
                    "result": result,
                })
        if not confirmation_items:
            return None
        return parser.confirmations.queue_dynamic_code(
            parser,
            confirmation_items,
            {
                "mode": "ai_batch",
                "response": response,
                "actions": actions,
                "validation_issues": validation_issues,
                "user_input": user_input_str,
                "image_data": image_data,
                "use_api": bool(use_api),
                "approved_skill_runs": sorted(approved_skills),
            },
            session_id,
            user_input_str,
            prior_approvals=approved,
        )

    @staticmethod
    def ensure_input_focus_steps(plan):
        """Bind keyboard input to an app and insert a verified focus step."""
        if not isinstance(plan, list):
            return plan
        normalized = []
        active_target = ""
        focused_target = ""
        for raw_step in plan:
            if not isinstance(raw_step, dict):
                normalized.append(raw_step)
                continue
            step = dict(raw_step)
            action = step.get("action")
            target = str(step.get("target", "") or "").strip()
            if action == "open_app":
                active_target = target
                focused_target = ""
            elif action == "focus_window":
                active_target = target
                focused_target = target
            elif action in {"move_window", "window_state"} and target:
                active_target = target
                if focused_target != target:
                    focused_target = ""
            elif action in {"hotkey", "type_text"}:
                input_target = target or active_target
                if input_target:
                    step["target"] = input_target
                    if focused_target != input_target:
                        normalized.append({
                            "action": "focus_window",
                            "target": input_target,
                            "direction": "",
                            "keys": [],
                            "text": "",
                            "seconds": 0,
                            "x": 0,
                            "y": 0,
                            "width": 0,
                            "height": 0,
                            "_implicit": "input_focus",
                        })
                    active_target = input_target
                    focused_target = input_target
            normalized.append(step)
        return normalized
