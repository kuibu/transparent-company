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


def _auth_error(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _extract_api_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _auth_error("invalid authorization header")
        return token.strip()
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    return None


def _actor_from_api_key(api_key: str) -> Actor | None:
    settings = get_settings()
    key_map = {
        settings.agent_api_key: Actor(type="agent", id=settings.agent_actor_id),
        settings.human_api_key: Actor(type="human", id=settings.human_actor_id),
        settings.auditor_api_key: Actor(type="auditor", id=settings.auditor_actor_id),
        settings.system_api_key: Actor(type="system", id=settings.system_actor_id),
    }
    return key_map.get(api_key)


def get_actor(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Actor:
    settings = get_settings()
    if not settings.auth_enabled:
        return Actor(type="agent", id=settings.agent_actor_id)

    api_key = _extract_api_key(authorization, x_api_key)
    if not api_key:
        raise _auth_error("missing api key")

    actor = _actor_from_api_key(api_key)
    if actor is None:
        raise _auth_error("invalid api key")
    return actor


def require_human(actor: Actor) -> None:
    if actor.type != "human":
        raise HTTPException(status_code=403, detail="human signature required")


def require_roles(actor: Actor, allowed: set[str], detail: str = "insufficient role") -> None:
    if actor.type not in allowed:
        raise HTTPException(status_code=403, detail=detail)


def _token_key() -> bytes:
    settings = get_settings()
    return settings.token_signing_secret.encode("utf-8")


def create_one_time_token(
    subject: str,
    disclosure_id: str,
    ttl_seconds: int,
    token_id: str,
    issued_to_actor_type: str,
    issued_to_actor_id: str,
) -> str:
    now = int(time.time())
    payload = {
        "jti": token_id,
        "subject": subject,
        "disclosure_id": disclosure_id,
        "issued_to_actor_type": issued_to_actor_type,
        "issued_to_actor_id": issued_to_actor_id,
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
