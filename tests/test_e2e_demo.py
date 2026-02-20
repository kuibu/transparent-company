from __future__ import annotations

import json


def test_e2e_demo_flow(client):
    seed = client.post("/demo/seed")
    assert seed.status_code == 200
    payload = seed.json()

    public_id = payload["public_disclosure"]["disclosure_id"]
    investor_id = payload["investor_disclosure"]["disclosure_id"]

    public_detail = client.get(f"/disclosure/{public_id}")
    investor_detail = client.get(f"/disclosure/{investor_id}")
    assert public_detail.status_code == 200
    assert investor_detail.status_code == 200

    proof = client.get(
        f"/disclosure/{public_id}/proof",
        params={"metric_key": "revenue_cents", "group": json.dumps({})},
    )
    assert proof.status_code == 200

    anchor = client.get(f"/anchor/disclosure/{public_id}")
    assert anchor.status_code == 200
    assert anchor.json()["key"] == f"disclosure:{public_id}"

    period = payload["period"]
    pnl = client.get("/reports/pnl", params={"period": f"{period['start']}/{period['end']}"})
    assert pnl.status_code == 200
    assert "report" in pnl.json()
