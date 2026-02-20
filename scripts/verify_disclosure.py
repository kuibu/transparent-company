#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import requests

from app.disclosure.commitment import normalize_group_param
from app.ledger.canonical import canonical_json
from app.ledger.merkle import MerkleTree, hash_leaf_payload, verify_proof
from app.ledger.signing import load_role_key, verify_object


def _sort_key(payload: dict) -> tuple:
    return (
        payload["metric_key"],
        canonical_json(payload["group"]).decode("utf-8"),
        payload["period"]["start"],
        payload["period"]["end"],
    )


def recompute_summary_root(statement: dict) -> str:
    committed_leafs = statement.get("commitments", {}).get("leaf_payloads") or []
    if committed_leafs:
        sorted_leafs = sorted(committed_leafs, key=_sort_key)
        return MerkleTree([hash_leaf_payload(payload) for payload in sorted_leafs]).root

    period = statement["period"]
    policy_id = statement["policy_id"]
    policy_hash = statement["policy_hash"]

    leaves = []
    for metric_key, value in statement.get("metrics", {}).items():
        leaves.append(
            {
                "metric_key": metric_key,
                "group": {},
                "period": period,
                "value": int(value),
                "policy_id": policy_id,
                "policy_hash": policy_hash,
            }
        )
    for row in statement.get("grouped_metrics", []):
        payload = {
            "metric_key": row["metric_key"],
            "group": row.get("group", {}),
            "period": period,
            "value": int(row["value"]),
            "policy_id": policy_id,
            "policy_hash": policy_hash,
        }
        if "detail_root" in row:
            payload["detail_root"] = row["detail_root"]
        leaves.append(payload)

    leaves.sort(key=_sort_key)
    hashes = [hash_leaf_payload(payload) for payload in leaves]
    return MerkleTree(hashes).root


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify disclosure signature, Merkle root, and proof")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--disclosure-id", required=True)
    parser.add_argument("--metric-key", required=True)
    parser.add_argument("--group", default="{}", help="JSON object or k=v,k2=v2")
    parser.add_argument("--public-key", default="", help="agent Ed25519 public key (base64)")
    args = parser.parse_args()

    disclosure = requests.get(f"{args.base_url}/disclosure/{args.disclosure_id}", timeout=30)
    disclosure.raise_for_status()
    disclosure_data = disclosure.json()

    statement = disclosure_data["statement"]
    signature = disclosure_data["statement_signature"]
    public_key = args.public_key or load_role_key("agent").public_key_b64

    sig_ok = verify_object(statement, signature, public_key)
    recomputed_root = recompute_summary_root(statement)
    stated_root = statement["commitments"]["root_summary"]

    group = normalize_group_param(args.group)
    proof_resp = requests.get(
        f"{args.base_url}/disclosure/{args.disclosure_id}/proof",
        params={"metric_key": args.metric_key, "group": json.dumps(group, ensure_ascii=False)},
        timeout=30,
    )
    proof_resp.raise_for_status()
    proof_data = proof_resp.json()["proof"]

    leaf_hash = proof_data["leaf_hash"]
    proof_ok = verify_proof(leaf_hash, proof_data["proof"], proof_data["root_summary"])

    print(json.dumps(
        {
            "disclosure_id": args.disclosure_id,
            "signature_valid": sig_ok,
            "stated_root": stated_root,
            "recomputed_root": recomputed_root,
            "root_match": stated_root == recomputed_root,
            "proof_valid": proof_ok,
            "metric_key": args.metric_key,
            "group": group,
        },
        indent=2,
        ensure_ascii=False,
    ))

    if not (sig_ok and proof_ok and stated_root == recomputed_root):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
