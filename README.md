# Transparent Company MVP+ / 透明公司 MVP+

[中文](#zh-cn) | [English](#english)

---

## zh-CN

### 项目背景
传统公司对外披露通常是“结果导向”，外部只能看到报表，很难验证生成过程是否被篡改。

本项目实现一个“可验证经营”最小可用系统（MVP+）：
- 内部：全部经营动作进入不可变事件账本（append-only）
- 对外：按策略做“粒度可选”披露（public / investor / auditor）
- 可验证：披露项做 Merkle 承诺并提供 proof，声明用 Ed25519 签名
- 不可篡改封条：将披露 root 与关键回执摘要锚定到 immudb
- 可视化：用 Apache Superset 读取披露汇总表/视图

这对应“agent 主驾驶 + human 副驾驶”经营模式：
- agent 负责高频执行动作（接单、调度、记账草案、披露发布）
- human 负责法定与高风险动作（规则制定、例外审批、最终签署）
- auditor/public 无需信任内部人，只需验证数据链路与证明

### 核心能力
- 事件账本：hash chain（`prev_hash -> event_hash`）+ Ed25519 签名
- 三链一致：订单、库存、财务（Beancount）可重放复算
- 披露策略治理：`DisclosurePolicy` 版本化 + `policy_hash`
- 承诺与证明：Merkle root + inclusion proof
- 封条锚定：immudb（披露承诺和回执摘要）
- BI 看板：Superset 直接连接 `disclosure_*` 表与视图
- Agent 记忆：通过 OpenViking HTTP 会话（可回退本地）沉淀 CEO 决策记忆与使命上下文
- Agent + Skills：`SkillRegistry/SkillRouter/SkillExecutor` 插件化执行，且每次运行写入 `SkillRunStarted/SkillRunFinished/SkillRunFailed` 不可变审计事件

### 架构分层
- `app/ledger/*`: 事件 schema、canonical JSON、签名、Merkle、anchoring、receipt hash
- `app/domain/*`: 订单/库存投影、财务分录与 P&L
- `app/reconciliation/*`: 三链一致规则
- `app/disclosure/*`: policy、指标计算、承诺、声明、选择性披露
- `app/persistence/*`: Postgres 模型与初始化
- `app/api/*`: FastAPI 路由
- `app/agent/*`: 主驾驶 Agent、记忆后端与工具连接器
- `app/agent/skills/*`: skills 解析、路由、执行与 entrypoint 注册
- `app/dashboard/superset/*`: Superset 初始化与自动注册脚本

### 关键口径
- 货币统一 `int cents`，禁止 float
- 时间统一 UTC（ISO8601 `Z`）
- Canonical JSON：排序 key + 紧凑编码，签名/哈希输入稳定
- `policy_hash = sha256(canonical_json(policy))`

Merkle leaf（确定性）：
```json
{
  "metric_key": "revenue_cents",
  "group": {"channel": "online"},
  "period": {"start": "...", "end": "..."},
  "value": 16500,
  "policy_id": "policy_public_v1",
  "policy_hash": "...",
  "detail_root": "... (optional)"
}
```
- `leaf_hash = sha256(canonical_json(leaf_payload))`
- 排序：`metric_key + canonical(group) + period_start + period_end`
- 奇数叶补最后一个，父节点 `sha256(left || right)`

### 项目结构
```text
app/
  main.py
  cli.py
  core/
  ledger/
  domain/
  reconciliation/
  disclosure/
  persistence/
  api/
  agent/
    skills/
  dashboard/superset/
skills/
scripts/
tests/
SKILLS.md
docker-compose.yml
```

### 一键启动
```bash
docker compose up -d --build
```

服务地址：
- API: `http://localhost:8000`
- Superset: `http://localhost:8088`（`admin/admin`）
- 默认看板: `http://localhost:8088/superset/dashboard/david-transparent-supermarket-story/`
- MinIO Console: `http://localhost:9001`（`minioadmin/minioadmin`）
- immudb gRPC: `localhost:3322`
- OpenViking API: `http://localhost:1933`

### Agent 记忆（OpenViking）
OpenViking 开源项目：`https://github.com/volcengine/openviking`

本项目新增了“有记忆的 agent 对话层”（HTTP API），用于让 CEO agent 记住：
- 自己的使命（mission）与 system prompt
- 历史对话中的关键决策
- 与不同对象（人类/其他 agent/审计者）的连续上下文

默认配置：
- `TC_AGENT_MEMORY_BACKEND=openviking_http`
- `TC_OPENVIKING_BASE_URL=http://openviking:1933`
- `TC_OPENVIKING_AUTO_COMMIT=true`
- `TC_OPENVIKING_FALLBACK_LOCAL=true`

说明：
- `docker-compose.yml` 已内置 `openviking` 服务，默认地址为 `http://openviking:1933`。
- 当前仓库运行的是 OpenViking-compatible HTTP service（接口兼容层），用于开箱即用；如需切到官方 OpenViking 实例，可仅替换 `TC_OPENVIKING_BASE_URL`。
- `app` 会在 `openviking` 健康后启动，默认直接使用 OpenViking 记忆后端。
- 若 OpenViking 临时不可达，系统仍会自动回退到本地记忆后端，不影响 API 使用。
- 当前实现对接 OpenViking HTTP 接口族：`/health`、`/api/v1/sessions`、`/messages`、`/commit`、`/search`。

### API 鉴权（新增）
敏感接口不再信任 `X-Actor-Type` 头，而是使用 API Key 身份映射：
- `tc-agent-dev-key`
- `tc-human-dev-key`
- `tc-auditor-dev-key`
- `tc-system-dev-key`

示例：
```bash
curl -H "X-API-Key: tc-human-dev-key" http://localhost:8000/ledger/full/events
```

### Skills（外挂式新增）
新增 `skills/` 体系，保持现有 API 与脚本兼容。

- 加载：`SkillRegistry` 扫描 `skills/*/SKILL.md`
- 路由：显式 `skill:<name>` 优先，其次关键词 `triggers` 匹配
- 执行：`SkillExecutor` 仅调用白名单 `entrypoint`（不做自然语言工具自动调用）
- 审计：每次执行写入 `SkillRunStarted`/`SkillRunFinished`/`SkillRunFailed` 不可变事件
- 风险门控：`permissions` 含 `exec` 或 `network` 视为高风险；显式调用需在 `SKILLS_APPROVED_LIST` 放行
- 自动路由门槛：`SKILLS_MAX_AUTOLOAD_RISK=low|high`
- 内置示例：`skills/procurement`（采购+入库）与 `skills/disclosure`（披露发布）

CLI 示例：
```bash
python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"
python -m app.cli agent run "skill:disclosure 披露昨日汇总 粒度=日"
```

扩展规范见 `SKILLS.md`。

### Demo（端到端）
服务启动后会自动自举默认故事数据（`TC_BOOTSTRAP_DEMO_ON_STARTUP=true`）：
- 场景 ID：`david_transparent_supermarket_q1_q2_story_v4`
- 场景版本：`3.1.0`
- 覆盖两个季度（2025 Q1 + Q2）：采购、收货、销售、退款、过期报损、供应商切换、顾客冲突与赔偿
- 角色分工：CEO Agent David 主驾驶；Human 法人徐大伟处理高风险/法定动作；Auditor 做数学验证
- 子 Agent：Sales/QC/Refund/Complaint/Logistics 全部写入同一不可篡改账本

默认披露层级与指标（当前版本）：
- 披露粒度：`day` + `week` + `month`（同一故事同时输出）
- 运营维度：`store_id`、`region`、`time_slot`、`promotion_phase`、`channel`、`category`（investor 口径含 `sku`）
- 指标覆盖：收入、退款率、客单价、复购率、库存周转天数、滞销 SKU 占比、质检不合格率、投诉闭环时长、经营现金净流入、供应商账期结构

**一句话（奶奶版）**
把公司想成一个透明小菜店：
- David 和一群小助手 agent 每天干活（进货、卖货、收钱、发货、退钱）
- 店主（Human）只管高风险大事（签合同、银行指令、线下调解）
- 每一步都有电子签名和“指纹封条”，任何人都能核验历史没被改过

查看默认故事：
```bash
curl http://localhost:8000/demo/default/story
```

公开层“汇总/全明细”可选（用户选择）：
```bash
# 汇总模式（默认，隐藏客户与银行对手方明细）
curl "http://localhost:8000/demo/default/story?detail_level=summary"

# 全明细实时模式（显式选择后返回完整细节）
curl "http://localhost:8000/demo/default/story?detail_level=full"
curl "http://localhost:8000/ledger/public/events?detail_level=full"
```

查看默认数据资产（CSV/JSON 导出路径 + 灵魂文件清单）：
```bash
curl http://localhost:8000/demo/default/assets
```

说明：demo 导出默认写入 `TC_DEMO_EXPORTS_ROOT`（docker 默认 `/tmp/transparent-company/demo-exports`），避免运行/测试时污染仓库跟踪文件。

获取 Superset 导入模板（JSON）：
```bash
curl http://localhost:8000/demo/default/superset-template
```

如需手动重放（幂等）：
```bash
curl -X POST http://localhost:8000/demo/seed
```

`/demo/seed` 关键返回字段（建议关注）：
- `scenario_id` / `scenario_version` / `seeded_now`
- `public_disclosure.disclosure_id`
- `investor_disclosure.disclosure_id`
- `public_daily_disclosures[]` / `public_weekly_disclosures[]` / `public_monthly_disclosures[]`
- `investor_weekly_disclosures[]` / `investor_monthly_disclosures[]`
- `superset.dashboard_url`

### 验证签名 / Root / Proof
```bash
python scripts/verify_disclosure.py \
  --base-url http://localhost:8000 \
  --disclosure-id <disclosure_id> \
  --metric-key revenue_cents
```

校验内容：
- 声明 Ed25519 验签
- 复算 `root_summary`
- Merkle proof 验证

### immudb 封条查询
系统写入的 key：
- `disclosure:{disclosure_id}`
- `root:summary:{period_start}:{policy_id}`
- `root:details:{period_start}:{policy_id}`（若有）
- `receipt:{receipt_hash}`

查询接口：
```bash
curl http://localhost:8000/anchor/disclosure/<disclosure_id>
```

锚定行为（新增）：
- 默认 `TC_ANCHOR_STRICT=true`，immudb 写入失败将直接报错（fail-closed），不会静默回退 fake。
- 仅在显式关闭 strict 时，才允许故障回退用于本地调试。

### Superset（已固化自动注册）
容器初始化时自动完成：
- 安装 PostgreSQL 驱动：`psycopg2-binary`
- 注册数据库连接：`TransparentCompanyPG`
- 注册 datasets：
  - `public.disclosure_runs`
  - `public.disclosure_metrics`
  - `public.disclosure_grouped_metrics`
  - `public.disclosure_public_daily_kpi_pretty`
  - `public.disclosure_public_weekly_kpi_pretty`
  - `public.disclosure_public_monthly_kpi_pretty`
  - `public.disclosure_investor_revenue_dimension_pretty`
  - `public.disclosure_investor_supplier_term_pretty`
- 自动创建 dashboard：`David Transparent Supermarket - Trust Dashboard`（slug: `david-transparent-supermarket-story`）
- 迁移行为：若历史存在旧 slug `transparent-company-default-story`，初始化时会自动迁移到新 slug 并清理旧默认看板

默认看板图表（10 个）：
- Daily Revenue Trend (CNY)
- Daily Net Operating Cashflow (CNY)
- Daily Average Order Value (CNY)
- Weekly Repeat Purchase Rate (%)
- Weekly QC Fail Rate (%)
- Weekly Complaint Resolution Hours
- Monthly Inventory Turnover Days
- Monthly Slow-moving SKU Ratio (%)
- Promotion Phase Revenue Mix (CNY)
- Supplier Payment Term Structure (CNY)

即使重建容器，数据库连接和 datasets 也会自动恢复。
若执行 `docker compose down -v` 清空业务数据卷，应用在下次启动时会自动重新写入默认故事数据（也可手动调用 `/demo/seed`）。

### API
- `POST /demo/seed`
- `GET  /demo/default/story`
- `GET  /demo/default/assets`
- `GET  /demo/default/superset-template`
- `GET  /disclosure/policies`
- `POST /disclosure/publish`
- `GET  /disclosure/{disclosure_id}`
- `GET  /disclosure/{disclosure_id}/proof?metric_key=...&group=...`
- `GET  /disclosure/{disclosure_id}/selective/request`
- `POST /disclosure/{disclosure_id}/selective/reveal`
- `GET  /anchor/disclosure/{disclosure_id}`
- `GET  /reports/pnl?period=start/end`
- `GET  /agent/memory/backend/health`
- `POST /agent/memory/profiles`
- `GET  /agent/memory/profiles/{agent_id}`
- `POST /agent/memory/conversations`
- `GET  /agent/memory/conversations/{conversation_id}`
- `POST /agent/memory/conversations/{conversation_id}/messages`
- `POST /agent/memory/conversations/{conversation_id}/chat`
- `POST /agent/memory/conversations/{conversation_id}/commit`
- `GET  /agent/memory/conversations/{conversation_id}/memory/search?q=...`

选择性披露安全约束（新增）：
- `selective/request` 与 `selective/reveal` 仅接受 `human/auditor` API key。
- 授权 token 为一次性（single-use）：首次 reveal 成功后即失效，重放会返回 `409`.

### 测试
推荐循环（容器内 Python 3.11 环境）：
```bash
docker compose exec app sh -lc 'cd /workspace && PYTHONPATH=/workspace pytest -q'
```

可选 smoke（skills CLI）：
```bash
docker compose exec app sh -lc 'cd /workspace && python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"'
```

覆盖：
- canonical 稳定性
- merkle root/proof
- signing 验签
- replay consistency
- disclosure proof
- e2e demo
- skills manifest 解析、路由与执行审计事件

### 安全与合规边界
- public 策略不输出客户/供应商可识别信息
- 披露口径由 policy 控制并版本化
- 选择性披露需授权，并写审计事件
- 历史事件不可修改，只能追加纠偏事件
- `proof_level=root_only` 的披露禁止 proof API 返回明细路径
- 治理引擎改为 default-deny（未匹配动作默认拒绝）

---

## English

### Project Background
Most corporate disclosure is result-oriented: outsiders see reports, but cannot verify whether the generation process was tampered with.

This project implements a verifiable operations MVP+:
- Internal: all operations are recorded in an immutable append-only event ledger
- External: policy-driven, audience-specific disclosure (public / investor / auditor)
- Verifiable: each disclosure metric is committed with Merkle root/proof and statements are Ed25519-signed
- Tamper-evident seal: disclosure roots and critical receipt digests are anchored in immudb
- Analytics: Apache Superset dashboards read disclosure summary tables/views

It matches an “agent as primary driver + human as copilot” model:
- Agent handles high-frequency operational actions
- Human handles legal/high-risk/sign-off actions
- Auditors/public verify proofs instead of trusting insiders

### Key Capabilities
- Event ledger: hash chain (`prev_hash -> event_hash`) + Ed25519 signatures
- Three-way consistency: orders, inventory, and accounting (Beancount) are replayable
- Policy governance: versioned `DisclosurePolicy` + `policy_hash`
- Commitment and proof: Merkle root + inclusion proof
- Anchoring: immudb for disclosure commitments and receipt digests
- BI dashboards: Superset on top of `disclosure_*` tables/views
- Agent memory: OpenViking HTTP sessions (with local fallback) preserve CEO mission and decision memory
- Agent + Skills runtime: plugin-style `SkillRegistry`/`SkillRouter`/`SkillExecutor` with immutable `SkillRunStarted`/`SkillRunFinished`/`SkillRunFailed` audit events

### Architecture Layers
- `app/ledger/*`: event schema, canonical JSON, signing, Merkle, anchoring, receipt hashing
- `app/domain/*`: order/inventory projections, accounting postings and P&L
- `app/reconciliation/*`: consistency checks
- `app/disclosure/*`: policy, metrics computation, commitments, statements, selective disclosure
- `app/persistence/*`: Postgres models and initialization
- `app/api/*`: FastAPI routes
- `app/agent/*`: primary-driver agent, memory backend, and connectors
- `app/agent/skills/*`: skill parsing, routing, execution, and entrypoint registry
- `app/dashboard/superset/*`: Superset init and auto-bootstrap scripts

### Key Conventions
- Money: `int cents` only (no float)
- Time: UTC only (ISO8601 `Z`)
- Canonical JSON: sorted keys + compact encoding for deterministic hash/signature input
- `policy_hash = sha256(canonical_json(policy))`

Deterministic Merkle leaf:
```json
{
  "metric_key": "revenue_cents",
  "group": {"channel": "online"},
  "period": {"start": "...", "end": "..."},
  "value": 16500,
  "policy_id": "policy_public_v1",
  "policy_hash": "...",
  "detail_root": "... (optional)"
}
```
- `leaf_hash = sha256(canonical_json(leaf_payload))`
- Leaf sort key: `metric_key + canonical(group) + period_start + period_end`
- For odd leaf count, duplicate last leaf; parent hash is `sha256(left || right)`

### Project Structure
```text
app/
  main.py
  cli.py
  core/
  ledger/
  domain/
  reconciliation/
  disclosure/
  persistence/
  api/
  agent/
    skills/
  dashboard/superset/
skills/
scripts/
tests/
SKILLS.md
docker-compose.yml
```

### Quick Start
```bash
docker compose up -d --build
```

Endpoints:
- API: `http://localhost:8000`
- Superset: `http://localhost:8088` (`admin/admin`)
- Default dashboard: `http://localhost:8088/superset/dashboard/david-transparent-supermarket-story/`
- MinIO Console: `http://localhost:9001` (`minioadmin/minioadmin`)
- immudb gRPC: `localhost:3322`
- OpenViking API: `http://localhost:1933`

### Agent Memory (OpenViking)
OpenViking open-source project: `https://github.com/volcengine/openviking`

This repo now includes a memory-aware agent conversation layer (HTTP API) so a CEO agent can retain:
- its mission and system prompt
- key historical decisions
- continuous context across human/agent/auditor conversations

Default config:
- `TC_AGENT_MEMORY_BACKEND=openviking_http`
- `TC_OPENVIKING_BASE_URL=http://openviking:1933`
- `TC_OPENVIKING_AUTO_COMMIT=true`
- `TC_OPENVIKING_FALLBACK_LOCAL=true`

Notes:
- `docker-compose.yml` now includes a built-in `openviking` service (default: `http://openviking:1933`).
- This repository runs an OpenViking-compatible HTTP service out of the box; to use an official OpenViking deployment, only change `TC_OPENVIKING_BASE_URL`.
- The `app` service waits for OpenViking health and uses it as the default memory backend.
- If OpenViking is temporarily unavailable, the system automatically falls back to local memory.
- Current integration covers OpenViking HTTP endpoints: `/health`, `/api/v1/sessions`, `/messages`, `/commit`, `/search`.

### API Authentication (New)
Sensitive endpoints no longer trust `X-Actor-Type`; they use API key identity mapping:
- `tc-agent-dev-key`
- `tc-human-dev-key`
- `tc-auditor-dev-key`
- `tc-system-dev-key`

Example:
```bash
curl -H "X-API-Key: tc-human-dev-key" http://localhost:8000/ledger/full/events
```

### Skills (Add-on, Non-breaking)
The project now supports an add-on `skills/` runtime without breaking existing APIs/scripts.

- Loading: `SkillRegistry` scans `skills/*/SKILL.md`
- Routing: explicit `skill:<name>` first, then trigger keyword matching
- Execution: `SkillExecutor` calls only registered code entrypoints (no NL auto tool invocation)
- Audit: every run writes immutable `SkillRunStarted`/`SkillRunFinished`/`SkillRunFailed` events
- Risk gate: any `permissions` containing `exec` or `network` is treated as high-risk; explicit run must be allow-listed in `SKILLS_APPROVED_LIST`
- Auto-route ceiling: `SKILLS_MAX_AUTOLOAD_RISK=low|high`
- Built-in examples: `skills/procurement` (procurement + goods received) and `skills/disclosure` (disclosure publish)

CLI examples:
```bash
python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"
python -m app.cli agent run "skill:disclosure 披露昨日汇总 粒度=日"
```

See `SKILLS.md` for authoring rules.

### End-to-End Demo
On startup, the stack auto-bootstraps the default storyline (`TC_BOOTSTRAP_DEMO_ON_STARTUP=true`):
- Scenario ID: `david_transparent_supermarket_q1_q2_story_v4`
- Scenario version: `3.1.0`
- Covers two quarters (2025 Q1 + Q2): procurement, receiving, sales, refunds, expiration loss, supplier switch, customer conflict, compensation
- Role split: CEO Agent David as primary driver, Human legal representative for high-risk/legal actions, Auditor for math-based verification
- Sub-agents (Sales/QC/Refund/Complaint/Logistics) all write to the same immutable ledger

Default disclosure scope (current build):
- Granularity: `day` + `week` + `month` from the same storyline
- Dimensions: `store_id`, `region`, `time_slot`, `promotion_phase`, `channel`, `category` (plus `sku` for investor views)
- KPI set: revenue, refund rate, average order value, repeat purchase rate, inventory turnover days, slow-moving SKU ratio, QC fail rate, complaint resolution hours, operating cash net inflow, supplier payment-term structure

**Grandma-friendly explanation**
Think of this like a transparent grocery shop:
- David and helper agents do daily operations (buy, sell, collect cash, ship, refund)
- The human owner only handles risky/legal tasks (contracts, bank instructions, on-site mediation)
- Every step is signed and sealed, so anyone can verify history was not modified

Inspect the default storyline:
```bash
curl http://localhost:8000/demo/default/story
```

Public disclosure mode is user-selectable:
```bash
# Summary mode (default; hides customer/bank counterparty details)
curl "http://localhost:8000/demo/default/story?detail_level=summary"

# Full realtime detail mode (explicit user choice)
curl "http://localhost:8000/demo/default/story?detail_level=full"
curl "http://localhost:8000/ledger/public/events?detail_level=full"
```

Inspect exported demo assets (CSV/JSON paths + soul manifest):
```bash
curl http://localhost:8000/demo/default/assets
```

Note: demo exports are written to `TC_DEMO_EXPORTS_ROOT` (docker default: `/tmp/transparent-company/demo-exports`) to avoid mutating tracked repository files during runs/tests.

Get Superset dashboard import template (JSON):
```bash
curl http://localhost:8000/demo/default/superset-template
```

Re-run seed manually (idempotent):
```bash
curl -X POST http://localhost:8000/demo/seed
```

Recommended `/demo/seed` response fields to inspect:
- `scenario_id` / `scenario_version` / `seeded_now`
- `public_disclosure.disclosure_id`
- `investor_disclosure.disclosure_id`
- `public_daily_disclosures[]` / `public_weekly_disclosures[]` / `public_monthly_disclosures[]`
- `investor_weekly_disclosures[]` / `investor_monthly_disclosures[]`
- `superset.dashboard_url`

### Verify Signature / Root / Proof
```bash
python scripts/verify_disclosure.py \
  --base-url http://localhost:8000 \
  --disclosure-id <disclosure_id> \
  --metric-key revenue_cents
```

Verification checks:
- Ed25519 statement signature
- recomputed `root_summary`
- Merkle proof validity

### immudb Anchoring Lookup
Written keys:
- `disclosure:{disclosure_id}`
- `root:summary:{period_start}:{policy_id}`
- `root:details:{period_start}:{policy_id}` (if present)
- `receipt:{receipt_hash}`

Lookup API:
```bash
curl http://localhost:8000/anchor/disclosure/<disclosure_id>
```

Anchoring behavior (new):
- Default `TC_ANCHOR_STRICT=true` is fail-closed: immudb anchor failures return error instead of silent fake fallback.
- Non-strict fallback is available only when explicitly disabled for local debugging.

### Superset (Auto-Bootstrapped)
During container init, the system automatically:
- installs PostgreSQL driver `psycopg2-binary`
- registers database connection `TransparentCompanyPG`
- registers datasets:
  - `public.disclosure_runs`
  - `public.disclosure_metrics`
  - `public.disclosure_grouped_metrics`
  - `public.disclosure_public_daily_kpi_pretty`
  - `public.disclosure_public_weekly_kpi_pretty`
  - `public.disclosure_public_monthly_kpi_pretty`
  - `public.disclosure_investor_revenue_dimension_pretty`
  - `public.disclosure_investor_supplier_term_pretty`
- auto-creates dashboard: `David Transparent Supermarket - Trust Dashboard` (slug: `david-transparent-supermarket-story`)
- migration behavior: if legacy slug `transparent-company-default-story` exists, bootstrap automatically migrates to the new slug and removes the old default dashboard

Default dashboard charts (10):
- Daily Revenue Trend (CNY)
- Daily Net Operating Cashflow (CNY)
- Daily Average Order Value (CNY)
- Weekly Repeat Purchase Rate (%)
- Weekly QC Fail Rate (%)
- Weekly Complaint Resolution Hours
- Monthly Inventory Turnover Days
- Monthly Slow-moving SKU Ratio (%)
- Promotion Phase Revenue Mix (CNY)
- Supplier Payment Term Structure (CNY)

After rebuilding containers, DB connection and datasets are restored automatically.
If you run `docker compose down -v`, business data volumes are cleared; on next startup the app auto-seeds the default storyline (you can still call `/demo/seed` manually).

### API
- `POST /demo/seed`
- `GET  /demo/default/story`
- `GET  /demo/default/assets`
- `GET  /demo/default/superset-template`
- `GET  /disclosure/policies`
- `POST /disclosure/publish`
- `GET  /disclosure/{disclosure_id}`
- `GET  /disclosure/{disclosure_id}/proof?metric_key=...&group=...`
- `GET  /disclosure/{disclosure_id}/selective/request`
- `POST /disclosure/{disclosure_id}/selective/reveal`
- `GET  /anchor/disclosure/{disclosure_id}`
- `GET  /reports/pnl?period=start/end`
- `GET  /agent/memory/backend/health`
- `POST /agent/memory/profiles`
- `GET  /agent/memory/profiles/{agent_id}`
- `POST /agent/memory/conversations`
- `GET  /agent/memory/conversations/{conversation_id}`
- `POST /agent/memory/conversations/{conversation_id}/messages`
- `POST /agent/memory/conversations/{conversation_id}/chat`
- `POST /agent/memory/conversations/{conversation_id}/commit`
- `GET  /agent/memory/conversations/{conversation_id}/memory/search?q=...`

Selective disclosure security (new):
- `selective/request` and `selective/reveal` accept only `human/auditor` API keys.
- The reveal token is single-use; replay attempts return `409`.

### Tests
Recommended test loop (inside the Python 3.11 app container):
```bash
docker compose exec app sh -lc 'cd /workspace && PYTHONPATH=/workspace pytest -q'
```

Optional smoke (skills CLI):
```bash
docker compose exec app sh -lc 'cd /workspace && python -m app.cli agent run "skill:procurement 今天进100斤青菜 供货商A 单价3.2"'
```

Coverage:
- canonical JSON stability
- Merkle root/proof
- signing verification
- replay consistency
- disclosure proof
- end-to-end demo
- skills manifest parsing, routing, and execution audit events

### Security & Compliance Boundaries
- Public policy does not expose personally/supplier-identifiable fields
- Disclosure granularity is policy-controlled and versioned
- Selective disclosure requires authorization and audit trail
- Historical events are immutable; corrections are append-only
- `proof_level=root_only` disclosures do not serve proof paths from the proof API
- Governance engine is now default-deny for unmatched actions
