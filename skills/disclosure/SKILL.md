---
name: disclosure
entrypoint: disclosure.run
description: Publish disclosure metrics and commitments (Merkle roots) for a period.
triggers:
  - 披露
  - disclosure
  - publish
  - merkle
  - root
permissions:
  - disclosure_publish
  - ledger_write
---
# Disclosure Skill SOP

1. Infer audience policy from query text (`public`, `investor`, or `auditor`).
2. Infer period granularity (`day`, `week`, or `month`).
3. Run disclosure publisher with policy controls and delay constraints.
4. Return disclosure id, period, metrics, and roots for verification.

Notes:
- This skill does not expose raw private details by default; policy gates still apply.
- Audit trail is mandatory through skill run ledger events.
