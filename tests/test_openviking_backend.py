from __future__ import annotations

import httpx

from app.agent.memory_backend import OpenVikingHTTPMemoryBackend
from app.core.config import Settings, get_settings


def _http_404(method: str, path: str) -> httpx.HTTPStatusError:
    request = httpx.Request(method, f"http://mock{path}")
    response = httpx.Response(status_code=404, request=request)
    return httpx.HTTPStatusError("not found", request=request, response=response)


def test_openviking_backend_adapter_falls_back_for_message_and_search_paths(monkeypatch):
    settings = get_settings()
    backend = OpenVikingHTTPMemoryBackend(settings)

    called_paths: list[str] = []

    def fake_request(method: str, path: str, **kwargs):
        called_paths.append(path)
        if path == "/api/v1/sessions":
            return {"status": "ok", "result": {"session_id": "sess-001"}}
        if path == "/api/v1/sessions/sess-001/add_message":
            raise _http_404(method, path)
        if path == "/api/v1/sessions/sess-001/messages":
            return {"status": "ok", "result": {"message_count": 2}}
        if path == "/api/v1/sessions/sess-001/commit":
            return {"status": "ok", "result": {"status": "committed"}}
        if path == "/api/v1/search/search":
            raise _http_404(method, path)
        if path == "/api/v1/search/find":
            return {
                "status": "ok",
                "result": {
                    "memories": [
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

    first_add = backend.add_message("sess-001", role="user", content="今天先做什么？")
    assert first_add["message_count"] == 2

    second_add = backend.add_message("sess-001", role="assistant", content="先核对现金流")
    assert second_add["message_count"] == 2

    committed = backend.commit("sess-001")
    assert committed["status"] == "committed"

    hits_first = backend.search("现金流", session_id="sess-001")
    assert len(hits_first) == 1
    assert hits_first[0].text.startswith("决策")
    assert hits_first[0].score == 0.91

    hits_second = backend.search("现金流", session_id="sess-001")
    assert len(hits_second) == 1

    message_calls = [p for p in called_paths if p.startswith("/api/v1/sessions/sess-001/") and p.endswith(("add_message", "messages"))]
    assert message_calls[:2] == [
        "/api/v1/sessions/sess-001/add_message",
        "/api/v1/sessions/sess-001/messages",
    ]
    assert message_calls[2:] == [
        "/api/v1/sessions/sess-001/messages",
    ]

    search_calls = [p for p in called_paths if p.startswith("/api/v1/search/")]
    assert search_calls[:2] == ["/api/v1/search/search", "/api/v1/search/find"]
    assert search_calls[2:] == ["/api/v1/search/find"]


def test_openviking_backend_failover_base_url(monkeypatch):
    settings = Settings(
        openviking_base_url="http://openviking-official:1933",
        openviking_fallback_base_url="http://openviking-compat:1933",
        openviking_timeout_seconds=2,
    )
    backend = OpenVikingHTTPMemoryBackend(settings)

    attempts: list[tuple[str, str]] = []

    def fake_request_once(base_url: str, method: str, path: str, *, json_body=None):
        attempts.append((base_url, path))
        if base_url == "http://openviking-official:1933":
            request = httpx.Request(method, f"{base_url}{path}")
            raise httpx.ConnectError("connection refused", request=request)
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/v1/sessions":
            return {"status": "ok", "result": {"session_id": "sess-fallback-001"}}
        raise AssertionError(f"unexpected path/base: {base_url} {path}")

    monkeypatch.setattr(backend, "_request_once", fake_request_once)

    health = backend.health()
    assert health.healthy is True
    assert attempts[:2] == [
        ("http://openviking-official:1933", "/health"),
        ("http://openviking-compat:1933", "/health"),
    ]

    attempts.clear()
    session_id = backend.create_session()
    assert session_id == "sess-fallback-001"
    assert attempts[0] == ("http://openviking-compat:1933", "/api/v1/sessions")
