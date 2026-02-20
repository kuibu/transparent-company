from __future__ import annotations

import json
from dataclasses import dataclass

from app.ledger.canonical import canonical_json
from app.ledger.merkle import MerkleTree, hash_leaf_payload


def proof_lookup_key(metric_key: str, group: dict) -> str:
    return metric_key + "|" + canonical_json(group).decode("utf-8")


@dataclass
class CommitmentResult:
    root_summary: str
    root_details: str | None
    leaf_payloads: list[dict]
    proof_index: dict[str, dict]
    detail_index: dict[str, dict]


def _sort_key(payload: dict) -> tuple:
    return (
        payload["metric_key"],
        canonical_json(payload["group"]).decode("utf-8"),
        payload["period"]["start"],
        payload["period"]["end"],
    )


def build_commitments(
    metrics: dict[str, int],
    grouped_metrics: list[dict],
    policy_id: str,
    policy_hash: str,
    period: dict,
    proof_level: str,
    detail_event_map: dict[str, list[str]] | None = None,
) -> CommitmentResult:
    detail_event_map = detail_event_map or {}
    base_leafs: list[dict] = []
    for metric_key, value in metrics.items():
        base_leafs.append(
            {
                "metric_key": metric_key,
                "group": {},
                "period": period,
                "value": int(value),
                "policy_id": policy_id,
                "policy_hash": policy_hash,
            }
        )

    for row in grouped_metrics:
        base_leafs.append(
            {
                "metric_key": row["metric_key"],
                "group": row.get("group", {}),
                "period": period,
                "value": int(row["value"]),
                "policy_id": policy_id,
                "policy_hash": policy_hash,
            }
        )

    base_leafs.sort(key=_sort_key)

    detail_index: dict[str, dict] = {}
    if proof_level == "selective_disclosure_ready":
        for leaf in base_leafs:
            metric_key = leaf["metric_key"]
            group = leaf["group"]
            lookup = proof_lookup_key(metric_key, group)
            detail_hashes = sorted(detail_event_map.get(lookup, []))
            if detail_hashes:
                detail_tree = MerkleTree(detail_hashes)
                detail_root = detail_tree.root
                detail_proofs = {
                    h: [node.__dict__ for node in detail_tree.proof(i)] for i, h in enumerate(detail_hashes)
                }
            else:
                detail_tree = MerkleTree([])
                detail_root = detail_tree.root
                detail_proofs = {}
            leaf["detail_root"] = detail_root
            detail_index[lookup] = {
                "detail_root": detail_root,
                "event_hashes": detail_hashes,
                "event_proofs": detail_proofs,
            }

    leaf_hashes = [hash_leaf_payload(payload) for payload in base_leafs]
    tree = MerkleTree(leaf_hashes)

    proof_index: dict[str, dict] = {}
    for i, payload in enumerate(base_leafs):
        lookup = proof_lookup_key(payload["metric_key"], payload.get("group", {}))
        proof_index[lookup] = {
            "leaf_hash": leaf_hashes[i],
            "leaf_payload": payload,
            "proof": [node.__dict__ for node in tree.proof(i)],
            "root_summary": tree.root,
            "position": i,
        }

    root_details = None
    if detail_index:
        detail_leafs = []
        for lookup in sorted(detail_index.keys()):
            detail_leafs.append({"lookup": lookup, "detail_root": detail_index[lookup]["detail_root"]})
        root_details = MerkleTree([hash_leaf_payload(x) for x in detail_leafs]).root

    return CommitmentResult(
        root_summary=tree.root,
        root_details=root_details,
        leaf_payloads=base_leafs,
        proof_index=proof_index,
        detail_index=detail_index,
    )


def normalize_group_param(raw_group: str | None) -> dict:
    if raw_group is None or raw_group == "":
        return {}
    if raw_group.strip().startswith("{"):
        return json.loads(raw_group)

    parsed: dict[str, str] = {}
    for part in raw_group.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        parsed[k.strip()] = v.strip()
    return parsed
