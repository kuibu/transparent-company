from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.skills.models import RiskLevel, SkillManifest, SkillRouteResult
from app.agent.skills.registry import SkillRegistry


RISK_ORDER: dict[RiskLevel, int] = {
    "low": 0,
    "high": 1,
}


def _normalize_risk(value: str | None) -> RiskLevel:
    raw = (value or "high").strip().lower()
    if raw not in RISK_ORDER:
        return "high"
    return raw  # type: ignore[return-value]


@dataclass(frozen=True)
class SkillRoutePolicy:
    max_autoload_risk: RiskLevel = "high"
    approved_list: frozenset[str] = frozenset()


class SkillRouter:
    EXPLICIT_PATTERN = re.compile(r"^\s*skill:([a-zA-Z0-9_-]+)\b", re.IGNORECASE)

    def __init__(self, registry: SkillRegistry, policy: SkillRoutePolicy | None = None):
        self.registry = registry
        self.policy = policy or SkillRoutePolicy()

    @classmethod
    def from_config(
        cls,
        registry: SkillRegistry,
        max_autoload_risk: str | None,
        approved_list_csv: str | None,
    ) -> "SkillRouter":
        approved = {
            item.strip() for item in (approved_list_csv or "").split(",") if item.strip()
        }
        policy = SkillRoutePolicy(
            max_autoload_risk=_normalize_risk(max_autoload_risk),
            approved_list=frozenset(approved),
        )
        return cls(registry=registry, policy=policy)

    def _high_risk_allowed(self, manifest: SkillManifest) -> bool:
        if manifest.risk_level == "low":
            return True
        return manifest.name in self.policy.approved_list

    def _autoload_allowed(self, manifest: SkillManifest) -> bool:
        if not self._high_risk_allowed(manifest):
            return False
        return RISK_ORDER[manifest.risk_level] <= RISK_ORDER[self.policy.max_autoload_risk]

    def route(self, query: str) -> SkillRouteResult | None:
        raw = query.strip()
        if not raw:
            return None

        explicit = self.EXPLICIT_PATTERN.match(raw)
        if explicit:
            name = explicit.group(1)
            manifest = self.registry.get(name)
            if manifest is None:
                return None
            if not self._high_risk_allowed(manifest):
                raise PermissionError(f"skill '{manifest.name}' is high risk and not in SKILLS_APPROVED_LIST")
            rewritten = raw[explicit.end() :].strip() or raw
            return SkillRouteResult(
                manifest=manifest,
                rewritten_query=rewritten,
                reason="explicit_skill_prefix",
            )

        query_lc = raw.lower()
        scored: list[tuple[int, SkillManifest]] = []
        for manifest in self.registry.all():
            if not self._autoload_allowed(manifest):
                continue

            score = 0
            for trigger in manifest.triggers:
                text = trigger.strip().lower()
                if text and text in query_lc:
                    score += 1
            if score > 0:
                scored.append((score, manifest))

        if not scored:
            return None

        scored.sort(key=lambda x: (-x[0], x[1].name))
        winner = scored[0][1]
        return SkillRouteResult(
            manifest=winner,
            rewritten_query=raw,
            reason="trigger_keyword_match",
        )
