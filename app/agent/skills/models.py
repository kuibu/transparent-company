from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


RiskLevel = Literal["low", "high"]


@dataclass(frozen=True)
class SkillManifest:
    name: str
    entrypoint: str
    description: str
    triggers: tuple[str, ...]
    permissions: tuple[str, ...]
    sop_markdown: str
    source_path: Path
    risk_level: RiskLevel


@dataclass(frozen=True)
class SkillRouteResult:
    manifest: SkillManifest
    rewritten_query: str
    reason: str


@dataclass(frozen=True)
class SkillExecutionResult:
    run_id: str
    skill_name: str
    entrypoint: str
    inputs_hash: str
    outputs_hash: str
    output: dict[str, Any]
