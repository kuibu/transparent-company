from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings


ActorType = Literal["agent", "human", "system", "auditor"]


class Actor(BaseModel):
    type: ActorType
    id: str


def get_actor(
    x_actor_type: str = Header(default="agent"),
    x_actor_id: str = Header(default="agent-001"),
) -> Actor:
    allowed = {"agent", "human", "system", "auditor"}
    if x_actor_type not in allowed:
        raise HTTPException(status_code=400, detail="invalid actor type")
    return Actor(type=x_actor_type, id=x_actor_id)


def require_human(actor: Actor) -> None:
    if actor.type != "human":
        raise HTTPException(status_code=403, detail="human signature required")


def _token_key() -> bytes:
    settings = get_settings()
    return settings.human_signing_key.encode("utf-8")


def create_one_time_token(subject: str, disclosure_id: str, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {
        "subject": subject,
        "disclosure_id": disclosure_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    mac = hmac.new(_token_key(), body, sha256).digest()
    return base64.urlsafe_b64encode(body + mac).decode("ascii")


def verify_one_time_token(token: str, disclosure_id: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail="invalid token encoding") from exc

    if len(raw) <= 32:
        raise HTTPException(status_code=400, detail="invalid token body")

    body, mac = raw[:-32], raw[-32:]
    expected = hmac.new(_token_key(), body, sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise HTTPException(status_code=401, detail="token signature mismatch")

    payload = json.loads(body.decode("utf-8"))
    if payload.get("disclosure_id") != disclosure_id:
        raise HTTPException(status_code=401, detail="token scope mismatch")
    if int(time.time()) > int(payload.get("exp", 0)):
        raise HTTPException(status_code=401, detail="token expired")
    return payload
