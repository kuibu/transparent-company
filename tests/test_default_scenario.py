from __future__ import annotations


def test_default_scenario_seed_and_story(client):
    first = client.post("/demo/seed")
    assert first.status_code == 200
    payload = first.json()

    assert payload["scenario_id"] == "default_transparent_company_story_v1"
    assert payload["seeded_now"] in {True, False}
    assert payload["identity_proof"]["all_valid"] is True
    assert payload["identity_proof"]["checked_event_count"] >= 3
    assert payload["public_disclosure"]["signer_role"] == "agent"
    assert payload["investor_disclosure"]["signer_role"] == "agent"

    human_signoffs = [
        item for item in payload["human_actions"] if item["event_type"] in {"ProcurementOrdered", "DisclosurePublished"}
    ]
    assert human_signoffs

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
