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

### 架构分层
- `app/ledger/*`: 事件 schema、canonical JSON、签名、Merkle、anchoring、receipt hash
- `app/domain/*`: 订单/库存投影、财务分录与 P&L
- `app/reconciliation/*`: 三链一致规则
- `app/disclosure/*`: policy、指标计算、承诺、声明、选择性披露
- `app/persistence/*`: Postgres 模型与初始化
- `app/api/*`: FastAPI 路由
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
  core/
  ledger/
  domain/
  reconciliation/
  disclosure/
  persistence/
  api/
  dashboard/superset/
scripts/
tests/
docker-compose.yml
```

### 一键启动
```bash
docker compose up -d --build
```

服务地址：
- API: `http://localhost:8000`
- Superset: `http://localhost:8088`（`admin/admin`）
- MinIO Console: `http://localhost:9001`（`minioadmin/minioadmin`）
- immudb gRPC: `localhost:3322`

### Demo（端到端）
触发业务演示数据：
```bash
curl -X POST http://localhost:8000/demo/seed
```

覆盖流程：
- 采购入库 -> 销售出库 -> 收款回执
- 自动财务分录与最小 P&L
- public/investor 披露发布
- 生成 root + 签名 + immudb anchor

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

### Superset（已固化自动注册）
容器初始化时自动完成：
- 安装 PostgreSQL 驱动：`psycopg2-binary`
- 注册数据库连接：`TransparentCompanyPG`
- 注册 datasets：
  - `public.disclosure_runs`
  - `public.disclosure_metrics`
  - `public.disclosure_grouped_metrics`
  - `public.disclosure_public_daily`
  - `public.disclosure_investor_grouped`

即使重建容器，数据库连接和 dataset 也会自动恢复；
注意：若执行 `docker compose down -v`，业务数据卷会清空，需重新执行 `/demo/seed` 填充数据。

### API
- `POST /demo/seed`
- `GET  /disclosure/policies`
- `POST /disclosure/publish`
- `GET  /disclosure/{disclosure_id}`
- `GET  /disclosure/{disclosure_id}/proof?metric_key=...&group=...`
- `GET  /disclosure/{disclosure_id}/selective/request`
- `POST /disclosure/{disclosure_id}/selective/reveal`
- `GET  /anchor/disclosure/{disclosure_id}`
- `GET  /reports/pnl?period=start/end`

### 测试
```bash
PYTHONPATH=/workspace pytest -q
```

覆盖：
- canonical 稳定性
- merkle root/proof
- signing 验签
- replay consistency
- disclosure proof
- e2e demo

### 安全与合规边界
- public 策略不输出客户/供应商可识别信息
- 披露口径由 policy 控制并版本化
- 选择性披露需授权，并写审计事件
- 历史事件不可修改，只能追加纠偏事件

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

### Architecture Layers
- `app/ledger/*`: event schema, canonical JSON, signing, Merkle, anchoring, receipt hashing
- `app/domain/*`: order/inventory projections, accounting postings and P&L
- `app/reconciliation/*`: consistency checks
- `app/disclosure/*`: policy, metrics computation, commitments, statements, selective disclosure
- `app/persistence/*`: Postgres models and initialization
- `app/api/*`: FastAPI routes
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
  core/
  ledger/
  domain/
  reconciliation/
  disclosure/
  persistence/
  api/
  dashboard/superset/
scripts/
tests/
docker-compose.yml
```

### Quick Start
```bash
docker compose up -d --build
```

Endpoints:
- API: `http://localhost:8000`
- Superset: `http://localhost:8088` (`admin/admin`)
- MinIO Console: `http://localhost:9001` (`minioadmin/minioadmin`)
- immudb gRPC: `localhost:3322`

### End-to-End Demo
Seed demo data:
```bash
curl -X POST http://localhost:8000/demo/seed
```

Flow coverage:
- procurement in -> sales out -> payment receipt
- automated accounting postings and minimal P&L
- public/investor disclosure publishing
- root + signature + immudb anchoring

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

### Superset (Auto-Bootstrapped)
During container init, the system automatically:
- installs PostgreSQL driver `psycopg2-binary`
- registers database connection `TransparentCompanyPG`
- registers datasets:
  - `public.disclosure_runs`
  - `public.disclosure_metrics`
  - `public.disclosure_grouped_metrics`
  - `public.disclosure_public_daily`
  - `public.disclosure_investor_grouped`

After rebuilding containers, DB connection and datasets are restored automatically.
If you run `docker compose down -v`, business data volumes are cleared; run `/demo/seed` again.

### API
- `POST /demo/seed`
- `GET  /disclosure/policies`
- `POST /disclosure/publish`
- `GET  /disclosure/{disclosure_id}`
- `GET  /disclosure/{disclosure_id}/proof?metric_key=...&group=...`
- `GET  /disclosure/{disclosure_id}/selective/request`
- `POST /disclosure/{disclosure_id}/selective/reveal`
- `GET  /anchor/disclosure/{disclosure_id}`
- `GET  /reports/pnl?period=start/end`

### Tests
```bash
PYTHONPATH=/workspace pytest -q
```

Coverage:
- canonical JSON stability
- Merkle root/proof
- signing verification
- replay consistency
- disclosure proof
- end-to-end demo

### Security & Compliance Boundaries
- Public policy does not expose personally/supplier-identifiable fields
- Disclosure granularity is policy-controlled and versioned
- Selective disclosure requires authorization and audit trail
- Historical events are immutable; corrections are append-only
