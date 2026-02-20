from __future__ import annotations

from typing import Literal

from app.ledger.signing import load_role_key


Role = Literal["agent", "human", "auditor"]


def expected_signer_role(actor_type: str) -> Role:
    if actor_type == "human":
        return "human"
    if actor_type == "auditor":
        return "auditor"
    # system actions are signed by agent in this MVP.
    return "agent"


def assert_signer_matches_actor(actor_type: str, signer_role: str) -> None:
    expected = expected_signer_role(actor_type)
    if signer_role != expected:
        raise ValueError(f"actor_type={actor_type} requires signer_role={expected}, got={signer_role}")


def public_key_manifest() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for role in ("agent", "human", "auditor"):
        key = load_role_key(role)
        out[role] = {
            "key_id": key.key_id,
            "algorithm": "Ed25519",
            "public_key_b64": key.public_key_b64,
        }
    return out
