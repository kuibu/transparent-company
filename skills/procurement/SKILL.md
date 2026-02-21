---
name: procurement
entrypoint: procurement.run
description: Create a procurement order and immediately record goods receipt.
triggers:
  - 采购
  - 进货
  - 入库
  - supplier
  - procurement
permissions:
  - ledger_write
  - inventory_write
---
# Procurement Skill SOP

1. Parse supplier, sku, quantity, and unit cost from user input.
2. Create a `ProcurementOrdered` event with policy `policy_internal_v1`.
3. Create a `GoodsReceived` event with a generated batch and expiry date.
4. Return identifiers and totals for audit-friendly CLI/API output.

Notes:
- This skill is intentionally low-risk: it does not execute shell commands or arbitrary network calls.
- All execution must be audited through `SkillRunStarted/Finished/Failed` events.
