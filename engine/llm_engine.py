import logging
import os

from engine.command_context import CommandContextBuilder
from engine.llm.gemini_provider import GeminiProvider
from engine.llm.openai_provider import OpenAIProvider
from engine.llm.prompts import (
    COMMAND_PROMPT_TEMPLATE,
    CONVERSATION_PROMPT_TEMPLATE,
    QUESTION_PROMPT_TEMPLATE,
)
from engine.llm.schemas import attach_command_candidates, decode_command_content
from engine.llm.service import LLMService
from engine.network import urlopen_verified
from engine.runtime_paths import user_data_path
from engine.storage.json_store import safe_read_json

USER_MEMORY_PATH = user_data_path("user_memory.json")
logger = logging.getLogger(__name__)
__all__ = [
    "COMMAND_PROMPT_TEMPLATE",
    "CONVERSATION_PROMPT_TEMPLATE",
    "LLMEngine",
    "QUESTION_PROMPT_TEMPLATE",
]


class LLMEngine:
    """Compatibility facade over provider and orchestration modules."""

    def __init__(self, dict_mgr):
        self.dict_mgr = dict_mgr
        self.timeout = 60
        self.command_context_builder = CommandContextBuilder(dict_mgr)
        self.last_command_context_stats = {}
        self.openai_provider = OpenAIProvider(self.timeout, self._decode_command_content)
        self.gemini_provider = GeminiProvider(self.timeout, self._decode_command_content)
        self.service = LLMService(self)

    @staticmethod
    def _decode_command_content(content):
        return decode_command_content(content)

    def _load_user_memory_context(self):
        if not os.path.exists(USER_MEMORY_PATH):
            return ""

        data = safe_read_json(USER_MEMORY_PATH, {})

        if not isinstance(data, dict):
            return ""

        sections = []
        user_info = data.get("user_info", data.get("userInfo", ""))
        rules = data.get("rules", "")
        others = data.get("others", "")

        if user_info:
            sections.append(f"[사용자 정보]\n{user_info}")
        if rules:
            sections.append(f"[응답 규칙]\n{rules}")
        if others:
            sections.append(f"[기타 기억]\n{others}")
        return "\n\n".join(sections)

    def _get_command_dictionary_context(self, user_input=""):
        """Compatibility helper returning the filtered app context."""
        return self.command_context_builder.build(user_input).dictionary_context

    @staticmethod
    def _attach_command_candidates(result, command_context):
        return attach_command_candidates(result, command_context)

    def _configure_provider(self, provider):
        provider.configure(self.timeout, self._decode_command_content)
        return provider

    def _call_openai(
        self,
        api_key,
        prompt,
        user_input,
        image_data=None,
        mode="command",
        temperature=0.2,
        stream_callback=None,
    ):
        provider = self._configure_provider(self.openai_provider)
        return provider.call(
            api_key,
            prompt,
            user_input,
            image_data=image_data,
            mode=mode,
            temperature=temperature,
            stream_callback=stream_callback,
            opener=urlopen_verified,
        )

    def _call_gemini(
        self,
        api_key,
        prompt,
        user_input,
        image_data=None,
        mode="command",
        temperature=0.2,
        stream_callback=None,
    ):
        provider = self._configure_provider(self.gemini_provider)
        return provider.call(
            api_key,
            prompt,
            user_input,
            image_data=image_data,
            mode=mode,
            temperature=temperature,
            stream_callback=stream_callback,
            opener=urlopen_verified,
        )

    def _invoke_provider(
        self,
        provider,
        api_key,
        prompt,
        user_input,
        image_data,
        mode,
        stream_callback,
    ):
        return self.service.invoke_provider(
            provider,
            api_key,
            prompt,
            user_input,
            image_data,
            mode,
            stream_callback,
        )

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
        return self.service.process_command(
            user_input,
            image_data=image_data,
            mode=mode,
            use_api=use_api,
            summary=summary,
            stream_callback=stream_callback,
            conversation_state=conversation_state,
        )
