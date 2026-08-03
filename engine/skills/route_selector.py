"""Select an execution route and a bounded, safe fallback chain for a skill."""

from __future__ import annotations

from dataclasses import dataclass

from engine.skills.skill_profile import SkillProfile

# Routes JARVIS can currently attempt. ``uia`` and ``native`` are alternate
# stored plans executed by the same ``ActionExecutor`` engine (they are not a
# new execution engine); stage 9 enriches the UIA locator ladder.
SUPPORTED_SKILL_ROUTES = frozenset({"native", "action_plan", "uia", "python"})

# Default preference when a skill does not pin an explicit ``primary_route``.
DEFAULT_ROUTE_PRIORITY = ("native", "action_plan", "uia", "python")

# Stage 7 allows at most one alternate attempt, regardless of stored profile.
MAX_FALLBACK_ATTEMPTS = 1

# Each non-python route maps to the plan key it executes through.
ROUTE_PLAN_KEYS = {
    "native": "native_plan",
    "action_plan": "plan",
    "uia": "uia_plan",
}


class SkillRouteUnavailable(ValueError):
    error_type = "validation_error"
    # Selecting nothing runnable never touches external state, so a caller may
    # safely treat it as a pre-mutation, fallback-eligible failure.
    route_failure_code = "route_unavailable"
    state_changed = False


@dataclass(frozen=True)
class RouteSelection:
    # ``route`` stays the primary/first-attempt route for backward compatibility.
    route: str
    available_routes: tuple[str, ...]
    fallback_routes: tuple[str, ...] = ()
    primary_route: str = ""
    max_fallback_attempts: int = 0
    reason: str = ""

    @property
    def attempt_chain(self):
        return (self.route, *self.fallback_routes)


class RouteSelector:
    """Choose the primary route and an ordered, available fallback chain."""

    @staticmethod
    def available_routes(skill, *, code_override=None):
        skill = skill if isinstance(skill, dict) else {}
        if code_override is not None:
            return ("python",) if str(code_override).strip() else ()

        routes = []
        for route in DEFAULT_ROUTE_PRIORITY:
            if route == "python":
                if str(skill.get("code", "")).strip():
                    routes.append("python")
            elif skill.get(ROUTE_PLAN_KEYS[route]):
                routes.append(route)
        return tuple(routes)

    def select(self, skill, *, profile=None, code_override=None):
        profile = profile or SkillProfile.from_skill(
            skill,
            route_override="python" if code_override is not None else None,
        )
        available = self.available_routes(skill, code_override=code_override)
        primary = profile.primary_route

        if primary and primary not in SUPPORTED_SKILL_ROUTES:
            raise SkillRouteUnavailable(
                f"지원하지 않는 스킬 실행 경로입니다: {primary or '없음'}"
            )

        # An adapted/override run is a one-off; it never falls back.
        max_attempts = (
            0
            if code_override is not None
            else min(profile.max_fallback_attempts, MAX_FALLBACK_ATTEMPTS)
        )

        fallbacks = []
        if max_attempts > 0:
            for route in profile.fallback_routes:
                if (
                    route in SUPPORTED_SKILL_ROUTES
                    and route in available
                    and route != primary
                    and route not in fallbacks
                ):
                    fallbacks.append(route)
            fallbacks = fallbacks[:max_attempts]

        primary_runnable = primary in SUPPORTED_SKILL_ROUTES and primary in available
        if not primary_runnable and not fallbacks:
            raise SkillRouteUnavailable(
                f"스킬 실행 경로 '{primary or '없음'}'에 필요한 실행 데이터가 없습니다."
            )

        reason = (
            "primary_route_available"
            if primary_runnable
            else "primary_route_unavailable"
        )
        return RouteSelection(
            route=primary,
            available_routes=available,
            fallback_routes=tuple(fallbacks),
            primary_route=primary,
            max_fallback_attempts=len(fallbacks),
            reason=reason,
        )
