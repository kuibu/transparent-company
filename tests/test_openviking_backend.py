from __future__ import annotations

from app.agent.memory_backend import OpenVikingHTTPMemoryBackend
from app.core.config import get_settings


def test_openviking_backend_adapter_parses_payloads(monkeypatch):
    settings = get_settings()
    backend = OpenVikingHTTPMemoryBackend(settings)

    def fake_request(method: str, path: str, **kwargs):
        if path == "/api/v1/sessions":
            return {"status": "ok", "result": {"session_id": "sess-001"}}
        if path == "/api/v1/sessions/sess-001/messages":
            return {"status": "ok", "result": {"message_count": 2}}
        if path == "/api/v1/sessions/sess-001/commit":
            return {"status": "ok", "result": {"status": "committed"}}
        if path == "/api/v1/search/search":
            return {
                "status": "ok",
                "result": {
                    "resources": [
                        {
                            "uri": "viking://session/sess-001/memory/1",
                            "abstract": "决策：优先现金流",
                            "score": 0.91,
                        }
                    ]
                },
            }
        if path == "/health":
            return {"status": "ok"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(backend, "_request", fake_request)

    health = backend.health()
    assert health.healthy is True

    session_id = backend.create_session()
    assert session_id == "sess-001"

    added = backend.add_message("sess-001", role="user", content="今天先做什么？")
    assert added["message_count"] == 2

    committed = backend.commit("sess-001")
    assert committed["status"] == "committed"

    hits = backend.search("现金流", session_id="sess-001")
    assert len(hits) == 1
    assert hits[0].text.startswith("决策")
    assert hits[0].score == 0.91
