from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import httpx

from app.core.config import Settings, get_settings


@dataclass
class MemorySearchHit:
    text: str
    score: float | None = None
    uri: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class MemoryBackendHealth:
    backend: str
    healthy: bool
    detail: str
    raw: dict[str, Any] | None = None


class AgentMemoryBackend(Protocol):
    backend_name: str

    def health(self) -> MemoryBackendHealth:
        ...

    def create_session(self, preferred_session_id: str | None = None) -> str:
        ...

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        ...

    def commit(self, session_id: str) -> dict[str, Any]:
        ...

    def search(self, query: str, session_id: str, limit: int = 5) -> list[MemorySearchHit]:
        ...


class OpenVikingHTTPMemoryBackend:
    backend_name = "openviking_http"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.openviking_base_url.rstrip("/")
        self.timeout = max(1, self.settings.openviking_timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.openviking_api_key:
            headers["X-API-Key"] = self.settings.openviking_api_key
        return headers

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(method, url, headers=self._headers(), json=json_body)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"result": payload}

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload

    def health(self) -> MemoryBackendHealth:
        try:
            payload = self._request("GET", "/health")
            status = str(payload.get("status", ""))
            is_ok = status.lower() in {"ok", "healthy"} or payload.get("result", {}).get("status") == "ok"
            detail = "connected" if is_ok else f"unexpected health payload: {payload}"
            return MemoryBackendHealth(backend=self.backend_name, healthy=is_ok, detail=detail, raw=payload)
        except Exception as exc:
            return MemoryBackendHealth(backend=self.backend_name, healthy=False, detail=str(exc), raw=None)

    def create_session(self, preferred_session_id: str | None = None) -> str:
        payload: dict[str, Any] = {}
        if preferred_session_id:
            payload["session_id"] = preferred_session_id
        raw = self._request("POST", "/api/v1/sessions", json_body=payload)
        result = self._unwrap(raw)
        session_id = result.get("session_id") if isinstance(result, dict) else None
        if not session_id:
            if preferred_session_id:
                return preferred_session_id
            raise RuntimeError(f"openviking create_session missing session_id: {raw}")
        return str(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        raw = self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/messages",
            json_body={"role": role, "content": content},
        )
        return self._unwrap(raw)

    def commit(self, session_id: str) -> dict[str, Any]:
        raw = self._request("POST", f"/api/v1/sessions/{session_id}/commit", json_body={})
        return self._unwrap(raw)

    def search(self, query: str, session_id: str, limit: int = 5) -> list[MemorySearchHit]:
        raw = self._request(
            "POST",
            "/api/v1/search/search",
            json_body={"query": query, "session_id": session_id, "limit": limit},
        )
        result = self._unwrap(raw)
        resources = result.get("resources", []) if isinstance(result, dict) else []

        hits: list[MemorySearchHit] = []
        if isinstance(resources, list):
            for item in resources:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("abstract") or item.get("content") or item.get("uri") or "")
                if not text:
                    continue
                score = item.get("score")
                hits.append(
                    MemorySearchHit(
                        text=text,
                        score=float(score) if isinstance(score, (int, float)) else None,
                        uri=item.get("uri"),
                        metadata=item,
                    )
                )
        return hits


class LocalMemoryBackend:
    backend_name = "local"

    def health(self) -> MemoryBackendHealth:
        return MemoryBackendHealth(backend=self.backend_name, healthy=True, detail="local backend ready", raw={"status": "ok"})

    def create_session(self, preferred_session_id: str | None = None) -> str:
        return preferred_session_id or f"local-{uuid4().hex[:16]}"

    def add_message(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        return {"session_id": session_id, "status": "ok", "role": role, "length": len(content)}

    def commit(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "status": "committed-local"}

    def search(self, query: str, session_id: str, limit: int = 5) -> list[MemorySearchHit]:
        return []


def build_memory_backend(settings: Settings | None = None) -> AgentMemoryBackend:
    cfg = settings or get_settings()
    if cfg.agent_memory_backend == "openviking_http":
        return OpenVikingHTTPMemoryBackend(cfg)
    return LocalMemoryBackend()
