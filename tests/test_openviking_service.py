from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_openviking_service_session_message_commit_search(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENVIKING_STORE_FILE", str(tmp_path / "openviking_store.json"))

    import app.agent.openviking_service as openviking_service

    openviking_service = importlib.reload(openviking_service)
    client = TestClient(openviking_service.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    created = client.post("/api/v1/sessions", json={"session_id": "sess-demo-001"})
    assert created.status_code == 200
    assert created.json()["result"]["session_id"] == "sess-demo-001"

    added = client.post(
        "/api/v1/sessions/sess-demo-001/messages",
        json={"role": "assistant", "content": "决策：先守住现金流再扩张"},
    )
    assert added.status_code == 200
    assert added.json()["result"]["message_count"] == 1

    committed = client.post("/api/v1/sessions/sess-demo-001/commit", json={})
    assert committed.status_code == 200
    assert committed.json()["result"]["status"] == "committed"

    searched = client.post(
        "/api/v1/search/search",
        json={"query": "现金流", "session_id": "sess-demo-001", "limit": 3},
    )
    assert searched.status_code == 200
    resources = searched.json()["result"]["resources"]
    assert len(resources) == 1
    assert resources[0]["uri"].startswith("viking://session/sess-demo-001/memory/")
