from __future__ import annotations


def test_full_ledger_requires_api_key_and_prevents_header_spoofing(client, auth_headers):
    no_auth = client.get("/ledger/full/events")
    assert no_auth.status_code == 401

    spoof_only = client.get(
        "/ledger/full/events",
        headers={"X-Actor-Type": "human", "X-Actor-Id": "spoof-human"},
    )
    assert spoof_only.status_code == 401

    agent_spoof = client.get(
        "/ledger/full/events",
        headers={
            **auth_headers["agent"],
            "X-Actor-Type": "human",
            "X-Actor-Id": "spoof-human",
        },
    )
    assert agent_spoof.status_code == 403

    human_ok = client.get("/ledger/full/events", headers=auth_headers["human"])
    assert human_ok.status_code == 200


def test_demo_story_supports_summary_and_full_detail_modes(client):
    seeded = client.post("/demo/seed")
    assert seeded.status_code == 200

    summary_story = client.get("/demo/default/story", params={"detail_level": "summary"})
    assert summary_story.status_code == 200
    summary_payload = summary_story.json()
    assert summary_payload["public_detail_level"] == "summary"
    assert "customers" not in summary_payload
    assert "bank_transactions" not in summary_payload
    assert "partners" not in summary_payload
    assert "customer_summary" in summary_payload
    assert "supplier_summary" in summary_payload

    full_story = client.get("/demo/default/story", params={"detail_level": "full"})
    assert full_story.status_code == 200
    full_payload = full_story.json()
    assert full_payload["public_detail_level"] == "full"
    assert "customers" in full_payload
    assert "bank_transactions" in full_payload
    assert "partners" in full_payload
