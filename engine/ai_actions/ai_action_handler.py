"""Facade for validation, preflight, and execution of AI action batches."""

from engine.ai_actions.batch_executor import BatchExecutor
from engine.ai_actions.batch_preflight import BatchPreflight
from engine.ai_actions.learning_descriptor import LearningDescriptor
from engine.ai_actions.result_validator import ResultValidator


class AIActionHandler:
    """Keep the public AI action API while delegating each responsibility."""

    def __init__(self, owner):
        self.owner = owner
        self.learning_descriptor = LearningDescriptor(owner)
        self.preflight = BatchPreflight(owner, self.learning_descriptor)
        self.validator = ResultValidator(owner, self.preflight)
        self.executor = BatchExecutor(owner)

    def handle_result(
        self,
        result,
        user_input,
        *,
        log_callback=None,
        session_id=None,
        image_data=None,
        use_api=False,
    ):
        response, actions, validation_issues = self.validate_result(result)
        return self.execute_batch(
            response,
            actions,
            validation_issues,
            user_input,
            log_callback=log_callback,
            session_id=session_id,
            image_data=image_data,
            use_api=use_api,
        )

    def validate_result(self, result):
        return self.validator.validate(result)

    def execute_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        preflight_gate = self.preflight.run(
            response,
            actions,
            validation_issues,
            user_input,
            session_id,
            log_callback=log_callback,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            image_data=image_data,
            use_api=use_api,
        )
        if preflight_gate is not None:
            return preflight_gate
        return self.executor.execute(
            response,
            actions,
            validation_issues,
            user_input,
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=approved_fingerprints,
            confirmation_id=confirmation_id,
            image_data=image_data,
            use_api=use_api,
        )

    def _ai_dynamic_descriptor(self, act):
        return self.learning_descriptor.describe(act)

    def _preflight_ai_action_batch(
        self,
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
        return self.preflight.run(
            response,
            actions,
            validation_issues,
            user_input_str,
            session_id,
            log_callback=log_callback,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            image_data=image_data,
            use_api=use_api,
        )

    def _execute_ai_action_batch(
        self,
        response,
        actions,
        validation_issues,
        user_input_str,
        *,
        log_callback=None,
        session_id=None,
        approved_fingerprints=None,
        approved_skill_runs=None,
        confirmation_id=None,
        image_data=None,
        use_api=False,
    ):
        return self.execute_batch(
            response,
            actions,
            validation_issues,
            user_input_str,
            log_callback=log_callback,
            session_id=session_id,
            approved_fingerprints=approved_fingerprints,
            approved_skill_runs=approved_skill_runs,
            confirmation_id=confirmation_id,
            image_data=image_data,
            use_api=use_api,
        )

    @staticmethod
    def _ensure_input_focus_steps(plan):
        return BatchPreflight.ensure_input_focus_steps(plan)

    def _validate_llm_result(self, result):
        return self.validator.validate(result)

    def _validate_plan_app_candidates(self, plan, learning, allowed_apps):
        return self.validator.validate_plan_app_candidates(
            plan, learning, allowed_apps
        )

    def _validate_generated_code(self, act, require_external_target=False):
        return self.validator.validate_generated_code(
            act, require_external_target=require_external_target
        )
