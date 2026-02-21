from __future__ import annotations

from pathlib import Path

from app.agent.skills.models import SkillManifest


def _parse_scalar(value: str):
    raw = value.strip()
    if not raw:
        return ""
    if raw.startswith(('"', "'")) and raw.endswith(('"', "'")) and len(raw) >= 2:
        return raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",") if part.strip()]
    if raw.isdigit():
        try:
            return int(raw)
        except Exception:
            return raw
    return raw


def parse_frontmatter_block(frontmatter: str) -> dict:
    data: dict[str, object] = {}
    lines = frontmatter.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line}")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if value:
            data[key] = _parse_scalar(value)
            i += 1
            continue

        i += 1
        items: list[object] = []
        while i < len(lines):
            next_line = lines[i].rstrip()
            next_stripped = next_line.strip()
            if not next_stripped:
                i += 1
                continue
            if next_stripped.startswith("- "):
                items.append(_parse_scalar(next_stripped[2:].strip()))
                i += 1
                continue
            if next_line.startswith(" ") or next_line.startswith("\t"):
                i += 1
                continue
            break
        data[key] = items

    return data


def split_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.lstrip().startswith("---"):
        return {}, markdown

    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown

    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break

    if closing_idx is None:
        raise ValueError("frontmatter opening found but closing --- missing")

    frontmatter = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :]).lstrip("\n")
    return parse_frontmatter_block(frontmatter), body


def _as_str_list(value: object) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.append(str(item).strip())
        return tuple(x for x in out if x)
    return (str(value),)


def _risk_from_permissions(permissions: tuple[str, ...]) -> str:
    lowered = {item.lower() for item in permissions}
    if "exec" in lowered or "network" in lowered:
        return "high"
    return "low"


def parse_skill_markdown(skill_file: Path) -> SkillManifest:
    raw = skill_file.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(raw)

    name = str(frontmatter.get("name") or skill_file.parent.name).strip()
    entrypoint = str(frontmatter.get("entrypoint") or "").strip()
    if not entrypoint:
        raise ValueError(f"skill '{name}' missing required entrypoint")

    description = str(frontmatter.get("description") or "").strip()
    triggers = _as_str_list(frontmatter.get("triggers"))
    permissions = _as_str_list(frontmatter.get("permissions"))
    risk = _risk_from_permissions(permissions)

    return SkillManifest(
        name=name,
        entrypoint=entrypoint,
        description=description,
        triggers=triggers,
        permissions=permissions,
        sop_markdown=body,
        source_path=skill_file,
        risk_level=risk,  # type: ignore[arg-type]
    )
