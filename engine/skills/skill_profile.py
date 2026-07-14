"""Execution-profile compatibility for learned JARVIS skills."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillProfile:
    """Describe the configured primary route and its bounded fallback policy.

    Stage 7 lets a skill store ``fallback_routes`` and ``max_fallback_attempts``
    so ``RouteSelector`` may attempt one safe alternate. Skills without an
    ``execution_profile`` keep their historical single-route behavior because
    ``fallback_routes`` defaults to empty and ``max_fallback_attempts`` to 0.
    """

    primary_route: str = ""
    fallback_routes: tuple[str, ...] = ()
    verification_required: bool = False
    max_fallback_attempts: int = 0

    @classmethod
    def from_skill(cls, skill, *, route_override=None):
        skill = skill if isinstance(skill, dict) else {}
        raw = skill.get("execution_profile", {})
        raw = raw if isinstance(raw, dict) else {}

        primary = str(route_override or raw.get("primary_route") or "").strip()
        if not primary:
            # Infer a default route from the stored plan data. ``plan`` keeps its
            # historical precedence so skills that already ran via action_plan are
            # unaffected; native_plan/uia_plan are added so a skill that only
            # carries those keys becomes runnable instead of failing selection.
            if skill.get("plan"):
                primary = "action_plan"
            elif skill.get("native_plan"):
                primary = "native"
            elif skill.get("uia_plan"):
                primary = "uia"
            elif str(skill.get("code", "")).strip():
                primary = "python"

        fallbacks = raw.get("fallback_routes", [])
        if not isinstance(fallbacks, (list, tuple)):
            fallbacks = []
        fallback_routes = tuple(
            route for route in (
                str(value or "").strip() for value in fallbacks
            )
            if route and route != primary
        )
        try:
            configured_attempts = int(raw.get("max_fallback_attempts", 0) or 0)
        except (TypeError, ValueError):
            configured_attempts = 0

        return cls(
            primary_route=primary,
            fallback_routes=fallback_routes,
            verification_required=bool(raw.get("verification_required", False)),
            max_fallback_attempts=max(0, configured_attempts),
        )

