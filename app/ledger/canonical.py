from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID


class CanonicalError(ValueError):
    pass


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    text = value.isoformat(timespec="microseconds")
    return text.replace("+00:00", "Z")


def to_canonical_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_canonical_obj(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [to_canonical_obj(v) for v in value]
    if isinstance(value, tuple):
        return [to_canonical_obj(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float):
        raise CanonicalError("float values are not allowed in canonical JSON")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, "model_dump"):
        return to_canonical_obj(value.model_dump())
    if hasattr(value, "dict"):
        return to_canonical_obj(value.dict())
    raise CanonicalError(f"unsupported canonical type: {type(value)!r}")


def canonical_json(value: Any) -> bytes:
    canonical = to_canonical_obj(value)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()
