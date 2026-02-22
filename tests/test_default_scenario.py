from __future__ import annotations


def test_default_scenario_seed_and_story(client):
    first = client.post("/demo/seed")
    assert first.status_code == 200
    payload = first.json()

    assert payload["scenario_id"] == "david_transparent_supermarket_q1_q2_story_v4"
    assert payload["seeded_now"] in {True, False}
    assert payload["identity_proof"]["all_valid"] is True
    assert payload["identity_proof"]["checked_event_count"] >= 3
    assert payload["public_disclosure"]["signer_role"] in {"agent", "human"}
    assert payload["investor_disclosure"]["signer_role"] == "agent"

    human_signoffs = [
        item
        for item in payload["human_actions"]
        if item["event_type"] in {"ProcurementOrdered", "DisclosurePublished", "SupplierContractSigned"}
    ]
    assert human_signoffs

    assert payload["soul_manifest"]
    assert all(item["path"].startswith("examples/transparent_supermarket/") for item in payload["soul_manifest"])
    assert payload["company"]["soul_manifest_hash"]
    assert payload["data_exports"]["events_json"].endswith("david_transparent_supermarket_q1_q2_events.json")

    second = client.post("/demo/seed")
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["seeded_now"] is False
    assert second_payload["scenario_id"] == payload["scenario_id"]

    story = client.get("/demo/default/story")
    assert story.status_code == 200
    story_payload = story.json()

    assert story_payload["scenario_id"] == payload["scenario_id"]
    assert story_payload["seeded_now"] is False
    assert story_payload["identity_proof"]["all_valid"] is True
    assert any(item["policy_id"] == "policy_auditor_v1" for item in story_payload["extra_disclosures"])
