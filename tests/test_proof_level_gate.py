from __future__ import annotations


def test_root_only_policy_disables_proof_endpoint(client, auth_headers):
    seeded = client.post("/demo/seed")
    assert seeded.status_code == 200
    period = seeded.json()["period"]

    publish = client.post(
        "/disclosure/publish",
        headers=auth_headers["agent"],
        json={
            "policy_id": "policy_public_root_only_v1",
            "period": f"{period['start']}/{period['end']}",
            "group_by": ["channel"],
        },
    )
    assert publish.status_code == 200
    disclosure_id = publish.json()["disclosure_id"]

    proof = client.get(
        f"/disclosure/{disclosure_id}/proof",
        params={"metric_key": "revenue_cents", "group": "{}"},
    )
    assert proof.status_code == 403
