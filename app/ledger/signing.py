from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from app.core.config import get_settings
from app.ledger.canonical import canonical_json


@dataclass(frozen=True)
class KeyMaterial:
    key_id: str
    signing_key: SigningKey

    @property
    def verify_key(self) -> VerifyKey:
        return self.signing_key.verify_key

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(bytes(self.verify_key)).decode("ascii")


def key_from_seed_b64(seed_b64: str, key_id: str) -> KeyMaterial:
    seed = base64.b64decode(seed_b64)
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    return KeyMaterial(key_id=key_id, signing_key=SigningKey(seed))


def load_role_key(role: str) -> KeyMaterial:
    settings = get_settings()
    if role == "agent":
        return key_from_seed_b64(settings.agent_signing_key, "agent")
    if role == "human":
        return key_from_seed_b64(settings.human_signing_key, "human")
    if role == "auditor":
        return key_from_seed_b64(settings.auditor_signing_key, "auditor")
    raise ValueError(f"unknown role key: {role}")


def sign_object(value: Any, key: KeyMaterial) -> str:
    payload = canonical_json(value)
    signature = key.signing_key.sign(payload).signature
    return base64.b64encode(signature).decode("ascii")


def verify_object(value: Any, signature_b64: str, public_key_b64: str) -> bool:
    payload = canonical_json(value)
    signature = base64.b64decode(signature_b64)
    verify_key = VerifyKey(base64.b64decode(public_key_b64))
    try:
        verify_key.verify(payload, signature)
        return True
    except BadSignatureError:
        return False
