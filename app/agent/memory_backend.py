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

    # Official-first intent: try add_message path first, then compat fallback path.
    MESSAGE_PATH_TEMPLATES = (
        "/api/v1/sessions/{session_id}/add_message",
        "/api/v1/sessions/{session_id}/messages",
    )
    SEARCH_PATHS = (
        "/api/v1/search/search",
        "/api/v1/search/find",
    )
    _COMPATIBLE_FALLBACK_STATUSES = {404, 405, 422}

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.primary_base_url = self.settings.openviking_base_url.rstrip("/")
        self.fallback_base_url = (
            self.settings.openviking_fallback_base_url.rstrip("/") if self.settings.openviking_fallback_base_url else None
        )
        self._active_base_url = self.primary_base_url
        self.timeout = max(1, self.settings.openviking_timeout_seconds)
        self._resolved_message_path_template: str | None = None
        self._resolved_search_path: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.openviking_api_key:
            headers["X-API-Key"] = self.settings.openviking_api_key
        return headers

    def _candidate_base_urls(self) -> list[str]:
        values = [self._active_base_url, self.primary_base_url, self.fallback_base_url]
        urls: list[str] = []
        for value in values:
            if value and value not in urls:
                urls.append(value)
        return urls

    def _request_once(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{base_url}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(method, url, headers=self._headers(), json=json_body)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"result": payload}

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for base_url in self._candidate_base_urls():
            try:
                payload = self._request_once(base_url, method, path, json_body=json_body)
                self._active_base_url = base_url
                return payload
            except Exception as exc:  # pragma: no cover - behavior validated via public methods
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("openviking request failed: no base url configured")

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload

    @staticmethod
    def _extract_resources(result: dict[str, Any]) -> list[dict[str, Any]]:
        direct = result.get("resources")
        if isinstance(direct, list):
            return [row for row in direct if isinstance(row, dict)]

        merged: list[dict[str, Any]] = []
        for key in ("memories", "resources", "skills"):
            rows = result.get(key)
            if isinstance(rows, list):
                merged.extend([row for row in rows if isinstance(row, dict)])
        return merged

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
        payload = {"role": role, "content": content}

        if self._resolved_message_path_template:
            resolved_path = self._resolved_message_path_template.format(session_id=session_id)
            try:
                raw = self._request("POST", resolved_path, json_body=payload)
                return self._unwrap(raw)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in self._COMPATIBLE_FALLBACK_STATUSES:
                    self._resolved_message_path_template = None
                else:
                    raise

        last_exc: Exception | None = None
        for path_template in self.MESSAGE_PATH_TEMPLATES:
            path = path_template.format(session_id=session_id)
            try:
                raw = self._request("POST", path, json_body=payload)
                self._resolved_message_path_template = path_template
                return self._unwrap(raw)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in self._COMPATIBLE_FALLBACK_STATUSES:
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("openviking add_message failed")

    def commit(self, session_id: str) -> dict[str, Any]:
        raw = self._request("POST", f"/api/v1/sessions/{session_id}/commit", json_body={})
        return self._unwrap(raw)

    def search(self, query: str, session_id: str, limit: int = 5) -> list[MemorySearchHit]:
        payload = {"query": query, "session_id": session_id, "limit": limit}

        if self._resolved_search_path:
            try:
                raw = self._request("POST", self._resolved_search_path, json_body=payload)
                result = self._unwrap(raw)
                resources = self._extract_resources(result) if isinstance(result, dict) else []
                return self._to_hits(resources)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in self._COMPATIBLE_FALLBACK_STATUSES:
                    self._resolved_search_path = None
                else:
                    raise

        last_exc: Exception | None = None
        for path in self.SEARCH_PATHS:
            try:
                raw = self._request("POST", path, json_body=payload)
                result = self._unwrap(raw)
                resources = self._extract_resources(result) if isinstance(result, dict) else []
                self._resolved_search_path = path
                return self._to_hits(resources)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in self._COMPATIBLE_FALLBACK_STATUSES:
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc is not None:
            raise last_exc
        return []

    @staticmethod
    def _to_hits(resources: list[dict[str, Any]]) -> list[MemorySearchHit]:
        hits: list[MemorySearchHit] = []
        for item in resources:
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
