"""One way to ask the configured provider for a JSON answer.

Three places needed this — edit-mode translation, macro composition and PDF
intent — and each grew its own copy of the same closure: read the dictionary
manager, read the AI config, pick ``_call_gemini`` or ``_call_openai``, call
with ``mode="json"``.  The third avoided a fourth copy by building a
translator it threw away and keeping its private ``_caller``.

A small object rather than a closure, so what it holds is visible: the
engine it calls, and nothing else from whatever scope built it.
"""

from __future__ import annotations


class ProviderCaller:
    """Ask the configured provider for a JSON reply, or return nothing."""

    __slots__ = ("_engine",)

    def __init__(self, engine):
        self._engine = engine

    def __call__(self, prompt: str, text: str):
        manager = getattr(self._engine, "dict_mgr", None)
        if manager is None:
            return ""
        try:
            config = manager.get_ai_config()
        except Exception:
            config = getattr(manager.config_manager, "ai_config", {})
        key = str((config or {}).get("api_key") or "").strip()
        if not key:
            # An unconfigured provider is the same as no provider: the rules
            # answer alone and the reader sees the usual guidance.
            return ""
        provider = str((config or {}).get("provider") or "openai").casefold()
        call = (
            self._engine._call_gemini
            if provider == "gemini"
            else self._engine._call_openai
        )
        return call(key, prompt, text, mode="json", temperature=0.0)


def provider_caller(engine) -> ProviderCaller | None:
    """A caller for this engine, or nothing when there is no engine."""
    return None if engine is None else ProviderCaller(engine)


__all__ = ["ProviderCaller", "provider_caller"]
