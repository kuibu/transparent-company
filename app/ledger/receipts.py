from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings


@dataclass(frozen=True)
class ReceiptRecord:
    object_key: str
    receipt_hash: str
    stored_at: datetime
    backend: str


class ReceiptStore:
    def put_json(self, object_key: str, payload: dict) -> ReceiptRecord:  # pragma: no cover - interface
        raise NotImplementedError


class LocalReceiptStore(ReceiptStore):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_json(self, object_key: str, payload: dict) -> ReceiptRecord:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        digest = sha256(data).hexdigest()
        path = self.root / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ReceiptRecord(
            object_key=object_key,
            receipt_hash=digest,
            stored_at=datetime.now(timezone.utc),
            backend="local",
        )


class MinioReceiptStore(ReceiptStore):
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        found = self.client.bucket_exists(self.bucket)
        if not found:
            self.client.make_bucket(self.bucket)

    def put_json(self, object_key: str, payload: dict) -> ReceiptRecord:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        digest = sha256(data).hexdigest()
        bio = io.BytesIO(data)
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=object_key,
            data=bio,
            length=len(data),
            content_type="application/json",
        )
        return ReceiptRecord(
            object_key=object_key,
            receipt_hash=digest,
            stored_at=datetime.now(timezone.utc),
            backend="minio",
        )


def build_receipt_store() -> ReceiptStore:
    settings = get_settings()
    if settings.receipt_backend == "minio":
        try:
            return MinioReceiptStore(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                bucket=settings.minio_bucket,
                secure=settings.minio_secure,
            )
        except S3Error:
            # During local tests if MinIO isn't available we degrade to local storage.
            pass
        except Exception:
            pass
    return LocalReceiptStore(settings.receipts_dir)
