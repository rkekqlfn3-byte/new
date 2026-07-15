from engine.execution_result import success_result


def resolve_demo(owner, context):
    consumed = context.consumed
    return success_result(
        "확인 카드 테스트를 정상적으로 완료했습니다.",
        action="confirmation_demo",
        verified=True,
        data={"confirmation_id": consumed["confirmation_id"]},
    )


def resolve_command_macro(owner, context):
    self = owner
    payload = context.payload
    return self.builtins.execute_cmd(
        {"data": payload.get("command", "")}, approved=True
    )
