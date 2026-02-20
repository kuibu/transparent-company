from __future__ import annotations

from app.ledger.signing import load_role_key, sign_object, verify_object


def test_sign_and_verify_ok():
    key = load_role_key("agent")
    payload = {"a": 1, "b": "ok"}
    sig = sign_object(payload, key)

    assert verify_object(payload, sig, key.public_key_b64)


def test_sign_verify_fail_when_payload_tampered():
    key = load_role_key("agent")
    payload = {"a": 1, "b": "ok"}
    sig = sign_object(payload, key)

    tampered = {"a": 2, "b": "ok"}
    assert not verify_object(tampered, sig, key.public_key_b64)
