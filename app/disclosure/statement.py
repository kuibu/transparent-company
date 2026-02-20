from __future__ import annotations

from datetime import datetime, timezone

from app.ledger.canonical import sha256_hex
from app.ledger.signing import KeyMaterial, sign_object


def build_statement(
    disclosure_id: str,
    policy_id: str,
    policy_hash: str,
    period: dict,
    metrics: dict[str, int],
    grouped_metrics: list[dict],
    root_summary: str,
    root_details: str | None,
    proof_level: str,
    leaf_payloads: list[dict] | None = None,
) -> dict:
    return {
        "disclosure_id": disclosure_id,
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "period": period,
        "metrics": metrics,
        "grouped_metrics": grouped_metrics,
        "commitments": {
            "root_summary": root_summary,
            "root_details": root_details,
            "proof_level": proof_level,
            "leaf_payloads": leaf_payloads or [],
            "leaf_schema": {
                "fields": ["metric_key", "group", "period", "value", "policy_id", "policy_hash", "detail_root"],
            },
        },
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def sign_statement(statement: dict, signer: KeyMaterial) -> tuple[str, str]:
    signature = sign_object(statement, signer)
    sig_hash = sha256_hex({"signature": signature, "statement": statement.get("disclosure_id")})
    return signature, sig_hash
