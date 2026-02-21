# Skills Guide

This project supports an add-on **skills** system under `skills/*/SKILL.md`.

## Skill File Format (`SKILL.md`)

Each skill is a folder with one `SKILL.md` file that starts with YAML frontmatter.

```md
---
name: procurement
entrypoint: procurement.run
description: Create procurement and goods-received events.
triggers:
  - 采购
  - procurement
permissions:
  - ledger_write
---
# SOP
...human-readable operating steps...
```

Required frontmatter fields:
- `name`: unique skill name
- `entrypoint`: registered Python callable id (`module_function` registry key)

Optional fields:
- `description`
- `triggers`: keyword list for auto-routing
- `permissions`: declarative permission labels (used for risk classification)

## Runtime Components

- `SkillRegistry`: scans `skills/*/SKILL.md`, parses frontmatter, builds index.
- `SkillRouter`: selection order:
  1. explicit `skill:<name>` prefix
  2. keyword trigger match
  3. fallback `None`
- `SkillExecutor`: executes only registered `entrypoint` functions and writes immutable audit events:
  - `SkillRunStarted`
  - `SkillRunFinished`
  - `SkillRunFailed`

## Security / Risk Controls

- No natural-language auto tool execution.
- Only pre-registered Python entrypoints are callable.
- Skills with `permissions` containing `exec` or `network` are classified as `high` risk.
- Optional env controls:
  - `SKILLS_MAX_AUTOLOAD_RISK=low|high`
  - `SKILLS_APPROVED_LIST=comma,separated,skill,names`

## Built-in Demo Skills

- `skills/procurement/SKILL.md` -> `procurement.run`
- `skills/disclosure/SKILL.md` -> `disclosure.run`

## CLI Demo

```bash
python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"
python -m app.cli agent run "skill:disclosure 披露昨日汇总 粒度=日"
```

