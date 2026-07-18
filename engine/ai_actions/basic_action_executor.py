import os

from engine.document_reader import extract_text
from engine.execution_result import failure_result
from engine.security.launch_policy import UnsafeLaunchTarget, validate_launch_target


def execute_open_app(context, action):
    target = action.get("target")
    if not target:
        return None
    path = action.get("_resolved_path")
    try:
        safe_path = validate_launch_target(path, target)
        context.log(f"[Execution] {safe_path} 켜는 중...")
        os.startfile(safe_path)
        if not safe_path.casefold().startswith(("http://", "https://")):
            context.parser.action_executor.focus_window_when_ready(target)
    except UnsafeLaunchTarget as error:
        context.log(f"[Security] 앱 실행 차단: {error}")
        context.add_response(f"\n\n{error}")
        context.failures.append(failure_result(
            str(error),
            action="open_app",
            target=target,
            error_type="validation_error",
            retryable=False,
            status="blocked",
        ))
    except Exception as error:
        context.log(f"[Error] 실행 실패: {error}")
        context.add_response(f"\n\n'{target}' 실행에 실패했습니다: {error}")
        context.failures.append(failure_result(
            str(error),
            action="open_app",
            target=target,
            error_type=context.parser._failure_type_for_error(error),
            retryable=False,
        ))
    return None


def execute_read_and_analyze(context, action):
    target = action.get("target")
    if not target:
        return None
    context.log(f"[Reader] 파일 읽는 중: {target}")
    extracted_text = extract_text(target)
    if "파일 읽기 실패" in extracted_text or "찾을 수 없습니다" in extracted_text:
        context.add_response(f"\n\n으앙 ㅠㅠ 파일을 읽는 데 실패했어: {extracted_text}")
        context.failures.append(failure_result(
            extracted_text,
            action="read_and_analyze",
            target=target,
            error_type="target_not_found",
        ))
        return None
    if not extracted_text.strip():
        context.add_response("\n\n파일은 찾았지만 분석할 수 있는 텍스트가 없습니다.")
        return None

    context.log("[LLM] 텍스트 추출 완료. 내용 분석 요청 중...")
    analyze_prompt = (
        f"원래 사용자의 요청: '{context.user_input}'\n\n"
        "아래 파일 내용을 바탕으로 위 요청에 완벽하게 대답해줘.\n\n"
        f"[파일 내용 시작]\n{extracted_text[:15000]}\n[파일 내용 끝]"
    )
    analyze_result = context.parser.llm_engine.process_command(
        analyze_prompt,
        context.image_data,
        mode="question",
        use_api=context.use_api,
    )
    analysis_text = (
        analyze_result.get("response", "")
        if isinstance(analyze_result, dict)
        else str(analyze_result or "")
    )
    context.add_response(
        "\n\n" + (analysis_text or "내용을 분석했지만 답변이 비어 있습니다.")
    )
    return None
