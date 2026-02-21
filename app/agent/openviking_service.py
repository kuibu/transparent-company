from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tokenize(value: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value) if t]


class SessionCreateRequest(BaseModel):
    session_id: str | None = None


class MessageAddRequest(BaseModel):
    role: str = Field(default="user")
    content: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str
    limit: int = Field(default=5, ge=1, le=50)


class VikingStore:
    def __init__(self, store_file: Path):
        self.store_file = store_file
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.store_file.exists():
            self.sessions = {}
            return
        try:
            payload = json.loads(self.store_file.read_text(encoding="utf-8"))
            sessions = payload.get("sessions") if isinstance(payload, dict) else None
            self.sessions = sessions if isinstance(sessions, dict) else {}
        except Exception:
            self.sessions = {}

    def _save(self) -> None:
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.store_file.with_suffix(".tmp")
        tmp_file.write_text(
            json.dumps({"sessions": self.sessions}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_file.replace(self.store_file)

    def create_session(self, preferred_session_id: str | None = None) -> str:
        with self.lock:
            session_id = preferred_session_id or f"sess-{uuid4().hex[:16]}"
            session = self.sessions.get(session_id)
            if not isinstance(session, dict):
                now = _utcnow()
                self.sessions[session_id] = {
                    "created_at": now,
                    "updated_at": now,
                    "messages": [],
                    "commit_count": 0,
                    "last_commit_at": None,
                }
                self._save()
            return session_id

    def _ensure_session(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if not isinstance(session, dict):
            self.create_session(preferred_session_id=session_id)
            session = self.sessions.get(session_id)
        assert isinstance(session, dict)
        session.setdefault("messages", [])
        session.setdefault("commit_count", 0)
        session.setdefault("last_commit_at", None)
        session.setdefault("created_at", _utcnow())
        session.setdefault("updated_at", _utcnow())
        return session

    def add_message(self, session_id: str, role: str, content: str) -> dict:
        with self.lock:
            session = self._ensure_session(session_id)
            messages = session["messages"]
            assert isinstance(messages, list)
            message_id = len(messages) + 1
            row = {
                "message_id": message_id,
                "role": role,
                "content": content,
                "created_at": _utcnow(),
            }
            messages.append(row)
            session["updated_at"] = _utcnow()
            self._save()
            return {
                "session_id": session_id,
                "message_id": message_id,
                "message_count": len(messages),
            }

    def commit(self, session_id: str) -> dict:
        with self.lock:
            session = self._ensure_session(session_id)
            messages = session.get("messages") or []
            commit_count = int(session.get("commit_count", 0)) + 1
            session["commit_count"] = commit_count
            session["last_commit_at"] = _utcnow()
            session["updated_at"] = _utcnow()
            self._save()
            return {
                "session_id": session_id,
                "status": "committed",
                "commit_count": commit_count,
                "message_count": len(messages),
                "last_commit_at": session["last_commit_at"],
            }

    def search(self, query: str, session_id: str, limit: int) -> list[dict]:
        with self.lock:
            session = self._ensure_session(session_id)
            messages = list(session.get("messages") or [])

        query_tokens = _tokenize(query)
        query_chars = {ch for ch in query.lower() if not ch.isspace()}

        scored: list[tuple[float, dict]] = []
        for idx, msg in enumerate(reversed(messages), start=1):
            content = str(msg.get("content", ""))
            content_lower = content.lower()
            if not content:
                continue

            token_overlap = sum(1 for token in query_tokens if token in content_lower)
            char_overlap = len(query_chars.intersection({ch for ch in content_lower if not ch.isspace()}))
            if query_tokens and token_overlap == 0 and char_overlap == 0:
                continue

            decision_bonus = 0.15 if str(msg.get("role")) == "assistant" else 0.0
            recency_bonus = max(0.0, 0.2 - (idx * 0.005))
            score = float(token_overlap) + (0.03 * float(char_overlap)) + decision_bonus + recency_bonus
            scored.append((score, msg))

        scored.sort(key=lambda item: item[0], reverse=True)
        resources: list[dict] = []
        for score, msg in scored[:limit]:
            message_id = int(msg.get("message_id", 0))
            snippet = str(msg.get("content", "")).strip()
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
            resources.append(
                {
                    "uri": f"viking://session/{session_id}/memory/{message_id}",
                    "abstract": snippet,
                    "score": round(score, 4),
                    "session_id": session_id,
                    "message_id": message_id,
                    "role": msg.get("role"),
                    "created_at": msg.get("created_at"),
                }
            )
        return resources

    def stats(self) -> dict:
        with self.lock:
            sessions = len(self.sessions)
            messages = sum(len((row.get("messages") or [])) for row in self.sessions.values() if isinstance(row, dict))
        return {"sessions": sessions, "messages": messages}


store_path = Path(os.getenv("OPENVIKING_STORE_FILE", "/data/openviking_store.json"))
store = VikingStore(store_path)
app = FastAPI(title="OpenViking Compatible Service", version="0.1.0")


@app.get("/health")
def health() -> dict:
    stats = store.stats()
    return {"status": "ok", "service": "openviking-compatible", **stats}


@app.post("/api/v1/sessions")
def create_session(request: SessionCreateRequest) -> dict:
    session_id = store.create_session(request.session_id)
    return {"status": "ok", "result": {"session_id": session_id}}


@app.post("/api/v1/sessions/{session_id}/messages")
def add_message(session_id: str, request: MessageAddRequest) -> dict:
    result = store.add_message(session_id=session_id, role=request.role, content=request.content)
    return {"status": "ok", "result": result}


@app.post("/api/v1/sessions/{session_id}/commit")
def commit_session(session_id: str) -> dict:
    result = store.commit(session_id)
    return {"status": "ok", "result": result}


@app.post("/api/v1/search/search")
def search_memory(request: SearchRequest) -> dict:
    resources = store.search(query=request.query, session_id=request.session_id, limit=request.limit)
    return {"status": "ok", "result": {"resources": resources}}
