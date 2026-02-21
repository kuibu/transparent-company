from __future__ import annotations


def _auditor_disclosure_id(seed_payload: dict) -> str:
    for item in seed_payload.get("extra_disclosures", []):
        if item.get("policy_id") == "policy_auditor_v1":
            return item["disclosure_id"]
    raise AssertionError("auditor disclosure not found")


def test_selective_disclosure_requires_role_and_token_is_one_time(client, auth_headers):
    seeded = client.post("/demo/seed")
    assert seeded.status_code == 200
    payload = seeded.json()
    disclosure_id = _auditor_disclosure_id(payload)

    denied_req = client.get(
        f"/disclosure/{disclosure_id}/selective/request",
        headers=auth_headers["agent"],
    )
    assert denied_req.status_code == 403

    ok_req = client.get(
        f"/disclosure/{disclosure_id}/selective/request",
        headers=auth_headers["auditor"],
        params={"subject": "audit-case-001"},
    )
    assert ok_req.status_code == 200
    token = ok_req.json()["token"]

    wrong_actor = client.post(
        f"/disclosure/{disclosure_id}/selective/reveal",
        headers=auth_headers["human"],
        json={"token": token, "metric_key": "revenue_cents", "group": {}},
    )
    assert wrong_actor.status_code == 403

    reveal_ok = client.post(
        f"/disclosure/{disclosure_id}/selective/reveal",
        headers=auth_headers["auditor"],
        json={"token": token, "metric_key": "revenue_cents", "group": {}},
    )
    assert reveal_ok.status_code == 200
    assert reveal_ok.json()["revealed_event_hashes"]

    replay = client.post(
        f"/disclosure/{disclosure_id}/selective/reveal",
        headers=auth_headers["auditor"],
        json={"token": token, "metric_key": "revenue_cents", "group": {}},
    )
    assert replay.status_code == 409
