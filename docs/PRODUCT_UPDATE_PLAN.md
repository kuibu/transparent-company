# Product Update Plan / 产品更新设计文档

## 1. Objective / 目标

This document turns the current README-level description into an implementable product plan.
目标是把当前“透明公司 + 默认透明超市示例”从技术演示提升为可持续迭代的产品路线图，并保持现有 API 与 demo 体验稳定。

## 2. Current State (Implemented) / 当前已实现

Based on the current README and codebase, the following are already in place:

- Immutable event ledger with hash chain + Ed25519 signatures
- Policy-governed disclosure (public/investor/auditor)
- Merkle commitment + proof + immudb anchoring
- Full/internal vs public disclosure modes (`detail_level=summary|full`)
- Superset auto-bootstrap with datasets/charts/dashboard
- Default two-quarter “David Transparent Supermarket” story
- Agent memory (OpenViking-compatible API) and skills runtime (registry/router/executor/audit events)
- Docker one-command startup and end-to-end seed flow

## 3. Product Gaps / 关键产品缺口

### P0 (must-have for product credibility)

1. Governance UX is still API-centric
- Gap: governance policies/rules are managed by files & code; no operator-facing workflow
- Impact: hard for non-engineers to review approvals and policy changes

2. Signer identity lifecycle is developer-level
- Gap: dev keys are default; no key rotation ceremony, revocation list, or signer lifecycle dashboard
- Impact: trust story is strong in theory but weak in operational governance

3. Disclosure explainability for non-technical users
- Gap: proofs exist, but the “why this metric changed” narrative is not productized
- Impact: investors/public can verify cryptography but may still not understand business meaning

4. Incident & exception workflow
- Gap: high-risk exceptions (break-glass, abnormal refund, conflict resolution) lack structured state machine UI/API
- Impact: compliance actions are recorded but not operationally orchestrated

### P1 (high value next)

5. Policy simulation before publish
- Gap: cannot dry-run “if policy X changed, what fields/metrics would change?”
- Impact: policy edits are risky and hard to reason about

6. Selective disclosure flow is minimal
- Gap: authorization challenge is basic; scope templates and expiry constraints are weak
- Impact: hard to scale auditor collaboration safely

7. Multi-tenant company support
- Gap: default architecture is single-company storyline oriented
- Impact: hard to reuse as a platform for multiple legal entities

### P2 (scale & operations)

8. Observability and SLOs
- Gap: no explicit SLO dashboard for proof generation latency, anchor failure rate, ledger append latency
- Impact: production reliability cannot be governed with objective targets

9. Data retention and legal hold policy
- Gap: no built-in retention tiers / legal hold policy layer
- Impact: long-term compliance and storage cost management are incomplete

## 4. Proposed Roadmap / 建议路线图

## Release A: Trust Operations Console (2-3 weeks)

### Scope
- Add governance console APIs and minimal pages:
  - policy versions list/diff/activate
  - approval queue for high-risk actions
  - signer registry (agent/human/auditor)
- Add auditable key lifecycle events:
  - `SignerKeyRotated`, `SignerRevoked`, `SignerActivated`

### Acceptance
- Non-engineer can complete policy update + approval + disclosure publish via console flow
- All governance actions appear in immutable audit events

## Release B: Disclosure Explainability (2 weeks)

### Scope
- Add “metric change narrative” API per disclosure run:
  - top drivers (volume/price/refund/loss/conﬂict)
  - period-over-period deltas
- Add Superset companion dataset for narrative metadata

### Acceptance
- For each published disclosure, API returns both proof and business explanation
- Dashboard shows trend + explanation card for the same period

## Release C: Controlled Selective Disclosure v2 (2 weeks)

### Scope
- Add scoped grant model:
  - who requested, why, what fields, expiry, max download count
- Add reveal session state machine:
  - `requested -> approved -> revealed -> expired`
- Add policy templates for auditor/investor/partner

### Acceptance
- Each reveal is bounded by scope + expiry and fully auditable
- Out-of-scope field requests are blocked by policy engine

## Release D: Platformization (3-4 weeks)

### Scope
- Multi-company partition key (`tenant_id`) across ledger/disclosure/views
- Per-tenant policy and dashboard bootstrap
- Per-tenant keyspace and anchor prefixes

### Acceptance
- Two tenants can run in same stack without data leakage
- Disclosure/anchor/proof queries are tenant-isolated by default

## 5. API Additions (Non-breaking) / 非破坏式接口增量

Keep existing endpoints unchanged. Add only new endpoints:

- `GET /governance/policies`
- `POST /governance/policies/{policy_id}/activate`
- `GET /governance/approvals`
- `POST /governance/approvals/{approval_id}/decide`
- `GET /signers`
- `POST /signers/{signer_id}/rotate-key`
- `GET /disclosure/{id}/explain`
- `POST /selective/grants`
- `POST /selective/grants/{id}/approve`
- `GET /selective/grants/{id}`

## 6. Metrics / 产品度量指标

- Trust metrics:
  - proof verification success rate
  - anchor write success rate
  - signature verification failure count
- Product metrics:
  - time-to-publish disclosure
  - high-risk approval lead time
  - selective disclosure turnaround time
- Reliability metrics:
  - p95 ledger append latency
  - p95 disclosure publish latency
  - Superset data freshness lag

## 7. Risks and Mitigations / 风险与对策

- Risk: adding UX can diverge from cryptographic truth model
  - Mitigation: UI must only read from signed/anchored state and immutable events
- Risk: key rotation can break verification for historical data
  - Mitigation: preserve signer key history and key-id in verification payload
- Risk: selective disclosure overexposure
  - Mitigation: scope templates + default-deny + auto-expiry + full audit trail

## 8. Implementation Priority / 实施优先级建议

1. Release A (governance + signer lifecycle)
2. Release B (explainability)
3. Release C (selective disclosure v2)
4. Release D (multi-tenant)

---

This plan is intentionally incremental and non-breaking: keep existing CLI/API/data formats stable while adding product-grade governance and usability.
本计划强调“外挂式增量升级”：不破坏现有接口与示例流程，在此基础上补齐产品化能力。
