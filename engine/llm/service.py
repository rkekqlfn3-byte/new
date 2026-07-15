import logging
import time

from engine.llm.prompts import (
    COMMAND_PROMPT_TEMPLATE,
    CONVERSATION_PROMPT_TEMPLATE,
    QUESTION_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, owner):
        self.owner = owner

    def invoke_provider(
        self,
        provider,
        api_key,
        ollama_model,
        prompt,
        user_input,
        image_data,
        mode,
        stream_callback,
    ):
        engine = self.owner
        if provider == "ollama":
            return engine._call_ollama(
                ollama_model, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        if provider == "openai":
            return engine._call_openai(
                api_key, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        if provider == "gemini":
            return engine._call_gemini(
                api_key, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        return {
            "response": "알 수 없는 AI 제공자입니다. 설정을 확인해주세요.",
            "action": "none", "target": None, "provider_error": True,
            "provider": provider,
        }

    def process_command(
        self,
        user_input,
        image_data=None,
        mode="command",
        use_api=False,
        summary="",
        stream_callback=None,
        conversation_state=None,
    ):
        engine = self.owner
        config = engine.dict_mgr.get_ai_config()
        provider = config.get("provider", "openai")
        api_key = config.get("api_key", "").strip()
        ollama_model = config.get("ollama_model", "llama3")
        routing_mode = config.get("routing_mode", "auto")

        system_prompt = "당신은 사용자의 컴퓨터 제어를 돕는 유능한 데스크탑 AI 어시스턴트입니다."

        if summary:
            system_prompt += f"\n\n[이전 대화 요약 (Context)]\n다음 정보는 최근까지의 핵심 대화 요약입니다. 이를 바탕으로 기억과 문맥을 자연스럽게 이어가세요:\n{summary}"

        memory_content = engine._load_user_memory_context()
        if memory_content:
            system_prompt += (
                "\n\n[사용자 영구 기억 (Personal Intelligence)]\n"
                "다음 정보는 사용자가 직접 저장한 기억입니다. 질문과 명령을 처리할 때 이 정보를 우선 참고하세요:\n"
                f"{memory_content}"
            )

        if mode == "command" and routing_mode in {"auto", "ai_first"}:
            actual_provider = provider if api_key else "ollama"
        else:
            actual_provider = provider if use_api else "ollama"

        if actual_provider != "ollama" and not api_key:
            return {"response": "API 키가 없습니다. [⚙️ AI 설정]에서 먼저 입력해주세요.", "action": "none", "target": None, "no_api_key": True}

        command_context = None
        if mode == "conversation":
            prompt = CONVERSATION_PROMPT_TEMPLATE.format(system_prompt=system_prompt)
        elif mode == "question":
            prompt = QUESTION_PROMPT_TEMPLATE.format(system_prompt=system_prompt)
        else:
            context_started = time.perf_counter()
            command_context = engine.command_context_builder.build(user_input)
            prompt = COMMAND_PROMPT_TEMPLATE.format(
                system_prompt=system_prompt,
                dictionary_context=command_context.dictionary_context,
                learned_macros_context=command_context.learned_macros_context,
            )
            engine.last_command_context_stats = {
                "app_candidates": command_context.app_candidates,
                "learned_candidates": command_context.learned_candidates,
                "prompt_chars": len(prompt),
                "selection_ms": round((time.perf_counter() - context_started) * 1000, 2),
                "provider": actual_provider,
            }
            logger.info(
                "AI command context: apps=%d macros=%d chars=%d selection_ms=%.2f",
                command_context.app_candidates,
                command_context.learned_candidates,
                len(prompt),
                engine.last_command_context_stats["selection_ms"],
            )

        provider_started = time.perf_counter()
        result = engine._invoke_provider(
            actual_provider, api_key, ollama_model, prompt, user_input,
            image_data, mode, stream_callback,
        )
        if (
            mode == "command"
            and routing_mode in {"auto", "ai_first"}
            and actual_provider != "ollama"
            and result.get("provider_error")
            and not result.get("tls_certificate_error")
        ):
            fallback = engine._invoke_provider(
                "ollama", api_key, ollama_model, prompt, user_input,
                image_data, mode, stream_callback,
            )
            if not fallback.get("provider_error"):
                fallback["routing"] = "ollama_fallback"
                fallback["fallback_from"] = actual_provider
                engine.last_command_context_stats["provider_ms"] = round(
                    (time.perf_counter() - provider_started) * 1000, 2
                )
                return engine._attach_command_candidates(fallback, command_context)
            result["response"] = (
                f"{result.get('response', '클라우드 AI 호출 실패')}\n\n"
                f"Ollama 대체도 실패했습니다: {fallback.get('response', '')}"
            )
            result["fallback_error"] = True
            engine.last_command_context_stats["provider_ms"] = round(
                (time.perf_counter() - provider_started) * 1000, 2
            )
            return engine._attach_command_candidates(result, command_context)

        result.setdefault(
            "routing",
            "ollama_no_api_key"
            if mode == "command" and actual_provider == "ollama" and not api_key
            else actual_provider,
        )
        if mode == "command":
            engine.last_command_context_stats["provider_ms"] = round(
                (time.perf_counter() - provider_started) * 1000, 2
            )
        return engine._attach_command_candidates(result, command_context)
