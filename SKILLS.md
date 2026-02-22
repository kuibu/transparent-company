# Skills Guide / Skills 使用指南

[中文](#中文) | [English](#english)

---

## 中文

本项目支持外挂式 **skills** 体系，目录约定为 `skills/*/SKILL.md`。

### 1) `SKILL.md` 文件格式

每个 skill 是一个独立文件夹，包含一个 `SKILL.md`，其开头为 YAML frontmatter。

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

必填字段：
- `name`: skill 唯一名称
- `entrypoint`: 已注册 Python 可调用入口名

可选字段：
- `description`: skill 描述
- `triggers`: 自动路由关键词
- `permissions`: 权限声明（用于风险分级）

### 2) 运行时组件

- `SkillRegistry`: 扫描 `skills/*/SKILL.md`，解析 frontmatter，建立索引
- `SkillRouter`: 路由优先级
  - 显式 `skill:<name>`
  - 关键词触发匹配
  - 未命中返回 `None`
- `SkillExecutor`: 只执行已注册 `entrypoint`，并写入不可变审计事件
  - `SkillRunStarted`
  - `SkillRunFinished`
  - `SkillRunFailed`

### 3) 安全与风险控制

- 不做“自然语言自动工具调用”
- 只允许调用已注册入口函数
- `permissions` 中含 `exec` 或 `network` 会被判定为高风险
- 环境变量控制：
  - `SKILLS_MAX_AUTOLOAD_RISK=low|high`
  - `SKILLS_APPROVED_LIST=skill_a,skill_b`

### 4) 内置示例 Skill

- `skills/procurement/SKILL.md` -> `procurement.run`
- `skills/disclosure/SKILL.md` -> `disclosure.run`

### 5) CLI 示例

```bash
python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"
python -m app.cli agent run "skill:disclosure 披露昨日汇总 粒度=日"
```

---

## English

This project supports an add-on **skills** runtime under `skills/*/SKILL.md`.

### 1) `SKILL.md` Format

Each skill lives in its own folder with one `SKILL.md` that starts with YAML frontmatter.

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

Required fields:
- `name`: unique skill name
- `entrypoint`: registered Python callable entrypoint

Optional fields:
- `description`
- `triggers`: keyword list for auto-routing
- `permissions`: declarative permissions for risk classification

### 2) Runtime Components

- `SkillRegistry`: scans `skills/*/SKILL.md`, parses frontmatter, builds index
- `SkillRouter`: routing priority
  - explicit `skill:<name>`
  - trigger keyword match
  - fallback `None`
- `SkillExecutor`: executes only registered entrypoints and writes immutable audit events
  - `SkillRunStarted`
  - `SkillRunFinished`
  - `SkillRunFailed`

### 3) Security and Risk Controls

- No natural-language auto tool execution
- Only pre-registered entrypoints are callable
- Skills containing `exec` or `network` in `permissions` are treated as high risk
- Environment controls:
  - `SKILLS_MAX_AUTOLOAD_RISK=low|high`
  - `SKILLS_APPROVED_LIST=skill_a,skill_b`

### 4) Built-in Demo Skills

- `skills/procurement/SKILL.md` -> `procurement.run`
- `skills/disclosure/SKILL.md` -> `disclosure.run`

### 5) CLI Examples

```bash
python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"
python -m app.cli agent run "skill:disclosure 披露昨日汇总 粒度=日"
```
