"""Propose a sequence of verified operations for a request none of them fits.

Layer 2 translates a sentence into **one** operation.  A request that needs
several — 표 만들고 첫 줄 굵게 해줘 — has no single operation to translate
into, so it fails even though every step it needs already exists and is
verified.

This is the third layer of the contract in
``JARVIS_ARCHITECTURE_CONTRACT.md``.  The important decision is what "make a
macro" means here: **the provider does not write code.**  It orders
operations that are already in the allow-list, with parameters those
operations already validate.  Nothing new becomes executable — the only new
thing is the order.

That keeps every guarantee the single-step path has.  Each step is still
prepared against live state, still previewed, still approved, still verified
and still undoable, because each step *is* one of those operations.

What this module does not do is run anything.  It returns a proposal for a
person to look at.  A plan that has never been seen by the reader is a plan
that has never been approved, and layer 3 exists to suggest, not to act.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from engine.edit_mode.llm_intent import (
    APP_LABELS,
    OPERATION_GUIDES,
    describe_selection,
)

# A proposal longer than this is not a macro, it is a program. The reader
# cannot check twenty steps against their own intent on a preview.
MAX_STEPS = 6

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class MacroStep:
    operation: str
    params: dict[str, Any]
    description: str


@dataclass(frozen=True, slots=True)
class MacroProposal:
    summary: str
    steps: tuple[MacroStep, ...]

    def as_text(self) -> str:
        lines = [f"{index}. {step.description}" for index, step in enumerate(self.steps, 1)]
        return "\n".join(lines)


def build_prompt(operations, app_type: str) -> str:
    """Ask for an ordering of existing operations, never for new code."""
    guide = OPERATION_GUIDES.get(app_type, {})
    allowed = sorted(set(operations) & set(guide))
    lines = [f"- {name}: {guide[name][0]}" for name in allowed]
    label = APP_LABELS.get(app_type, app_type)
    return (
        f"너는 {label} 편집 요청을 아래 작업들의 순서로 나눈다.\n"
        "새 기능을 지어내지 말고, 목록에 있는 작업만 순서대로 쓴다.\n"
        f"단계는 최대 {MAX_STEPS}개까지만 쓴다.\n"
        "목록에 있는 작업들로 요청을 만들 수 없으면 steps 를 빈 목록으로 둔다.\n\n"
        "작업 목록:\n" + "\n".join(lines) + "\n\n"
        '출력은 JSON 한 줄만: {"summary": "한 줄 설명", "steps": '
        '[{"operation": "...", "params": {...}, "description": "이 단계 설명"}]}\n'
        "params 에는 각 작업에 적힌 이름만 쓴다. 설명은 붙이지 마라."
    )


def _payload(raw) -> dict:
    if isinstance(raw, Mapping):
        candidate: Any = raw
    else:
        match = _JSON_OBJECT.search(str(raw or ""))
        if not match:
            return {}
        try:
            candidate = json.loads(match.group(0))
        except (TypeError, ValueError):
            return {}
    inner = candidate.get("response") if isinstance(candidate, Mapping) else None
    if isinstance(inner, str):
        match = _JSON_OBJECT.search(inner)
        if match:
            try:
                candidate = json.loads(match.group(0))
            except (TypeError, ValueError):
                return {}
    return candidate if isinstance(candidate, Mapping) else {}


class LlmMacroComposer:
    """Turn one unmet request into an ordering of allowed operations.

    Every rejection returns ``None`` so the caller falls back to the message
    it would have shown anyway: an empty plan, a step naming an operation
    outside the allow-list, a parameter that operation does not take, more
    steps than a person can check, an unreadable reply, or no network.
    """

    def __init__(self, caller: Callable[[str, str], Any]):
        self._caller = caller

    def compose(
        self, text: str, operations, app_type: str, context=None
    ) -> MacroProposal | None:
        guide = OPERATION_GUIDES.get(app_type)
        if not guide or not str(text or "").strip():
            return None
        allowed = set(operations) & set(guide)
        if not allowed:
            return None
        selection = describe_selection(context)
        message = f"{selection}\n요청: {text}" if selection else str(text)
        try:
            raw = self._caller(build_prompt(allowed, app_type), message)
        except Exception:
            return None
        payload = _payload(raw)
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return None
        if len(raw_steps) > MAX_STEPS:
            return None
        steps = []
        for item in raw_steps:
            step = self._step(item, allowed, guide)
            if step is None:
                return None
            steps.append(step)
        # One step is layer 2's job. Reaching here with a single step means
        # layer 2 already refused it, so proposing it again adds nothing.
        if len(steps) < 2:
            return None
        summary = str(payload.get("summary") or "").strip() or str(text)
        return MacroProposal(summary, tuple(steps))

    @staticmethod
    def _step(item, allowed, guide) -> MacroStep | None:
        if not isinstance(item, Mapping):
            return None
        operation = str(item.get("operation") or "").strip()
        if operation not in allowed:
            return None
        params = item.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        if not set(params) <= set(guide[operation][1]):
            return None
        description = str(item.get("description") or "").strip() or operation
        return MacroStep(operation, params, description)


def composer_for(llm_engine) -> LlmMacroComposer | None:
    """Wrap the configured provider, or nothing when there is none."""
    if llm_engine is None:
        return None
    from engine.edit_mode.llm_intent import translator_for

    translator = translator_for(llm_engine)
    if translator is None:
        return None
    return LlmMacroComposer(translator._caller)


__all__ = [
    "MAX_STEPS",
    "LlmMacroComposer",
    "MacroProposal",
    "MacroStep",
    "build_prompt",
    "composer_for",
]
