from __future__ import annotations

from pathlib import Path

from app.agent.skills.models import SkillManifest
from app.agent.skills.parser import parse_skill_markdown


class SkillRegistry:
    def __init__(self, root: Path, manifests: dict[str, SkillManifest]):
        self.root = root
        self._manifests = manifests

    @classmethod
    def load(cls, root: Path) -> "SkillRegistry":
        skill_root = root.expanduser().resolve()
        manifests: dict[str, SkillManifest] = {}
        if not skill_root.exists():
            return cls(root=skill_root, manifests={})

        for skill_dir in sorted([p for p in skill_root.iterdir() if p.is_dir()], key=lambda p: p.name):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            manifest = parse_skill_markdown(skill_file)
            if manifest.name in manifests:
                raise ValueError(f"duplicate skill name detected: {manifest.name}")
            manifests[manifest.name] = manifest

        return cls(root=skill_root, manifests=manifests)

    def get(self, name: str) -> SkillManifest | None:
        return self._manifests.get(name)

    def all(self) -> list[SkillManifest]:
        return [self._manifests[key] for key in sorted(self._manifests.keys())]

    def names(self) -> list[str]:
        return sorted(self._manifests.keys())
