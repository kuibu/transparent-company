from __future__ import annotations

import json

from app.ledger.merkle import verify_proof
from app.ledger.signing import verify_object


def test_publish_and_proof_api(client):
    seeded = client.post("/demo/seed")
    assert seeded.status_code == 200
    disclosure_id = seeded.json()["public_disclosure"]["disclosure_id"]

    disclosure = client.get(f"/disclosure/{disclosure_id}")
    assert disclosure.status_code == 200
    data = disclosure.json()

    proof_resp = client.get(
        f"/disclosure/{disclosure_id}/proof",
        params={"metric_key": "revenue_cents", "group": json.dumps({})},
    )
    assert proof_resp.status_code == 200
    proof = proof_resp.json()["proof"]

    assert verify_proof(proof["leaf_hash"], proof["proof"], proof["root_summary"])

    # Signature is verifiable with explicit key returned by publish API in demo payload.
    demo_details = seeded.json()
    public_key = demo_details.get("public_disclosure", {}).get("agent_public_key")
    if public_key:
        assert verify_object(data["statement"], data["statement_signature"], public_key)
