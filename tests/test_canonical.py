from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ledger.canonical import CanonicalError, canonical_json, sha256_hex


def test_canonical_json_stable_key_order():
    obj_a = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    obj_b = {"nested": {"x": 1, "y": 2}, "a": 1, "b": 2}

    assert canonical_json(obj_a) == canonical_json(obj_b)
    assert sha256_hex(obj_a) == sha256_hex(obj_b)


def test_canonical_json_rejects_float():
    with pytest.raises(CanonicalError):
        canonical_json({"amount": 1.23})


def test_canonical_datetime_normalized_to_utc_z():
    dt = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    payload = {"occurred_at": dt}
    encoded = canonical_json(payload).decode("utf-8")
    assert "Z" in encoded
