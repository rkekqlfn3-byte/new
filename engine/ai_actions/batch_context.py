from dataclasses import dataclass, field

from engine.execution_result import failure_result


@dataclass
class BatchExecutionContext:
    parser: object
    response: str
    actions: list
    user_input: str
    log_callback: object = None
    session_id: str | None = None
    approved_fingerprints: object = None
    confirmation_id: str | None = None
    image_data: object = None
    use_api: bool = False
    action_results: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    learnable_action_count: int = 0

    @classmethod
    def create(
        cls,
        parser,
        response,
        actions,
        validation_issues,
        user_input,
        **options,
    ):
        failures = []
        if validation_issues:
            response += "\n\n실행하지 않은 동작:\n- " + "\n- ".join(
                validation_issues
            )
            failures.append(failure_result(
                validation_issues[0],
                action="ai_validation",
                error_type="validation_error",
                data={"issues": validation_issues},
            ))
        learnable_count = sum(
            1
            for action_item in actions
            if action_item.get("action") in {"dynamic_code", "action_plan"}
        )
        return cls(
            parser=parser,
            response=response,
            actions=actions,
            user_input=user_input,
            failures=failures,
            learnable_action_count=learnable_count,
            **options,
        )

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)

    def add_response(self, message):
        self.response += message
