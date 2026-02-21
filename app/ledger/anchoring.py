from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import AnchorRecordModel

logger = logging.getLogger(__name__)


@dataclass
class AnchorWriteResult:
    key: str
    value: dict
    backend: str
    tx_id: str | None


class AnchorClient(Protocol):
    backend: str

    def set(self, key: str, value: dict) -> AnchorWriteResult:
        ...


class FakeAnchorClient:
    backend = "fake"

    def set(self, key: str, value: dict) -> AnchorWriteResult:
        tx_id = f"fake-{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        return AnchorWriteResult(key=key, value=value, backend=self.backend, tx_id=tx_id)


class ImmudbCliAnchorClient:
    backend = "immudb_cli"

    def __init__(self):
        self.settings = get_settings()

    def _base(self) -> list[str]:
        return [
            self.settings.immuclient_bin,
            "--address",
            self.settings.immudb_address,
            "--port",
            str(self.settings.immudb_port),
            "--username",
            self.settings.immudb_user,
            "--password",
            self.settings.immudb_password,
            "--database",
            self.settings.immudb_database,
        ]

    def _run(self, args: list[str]) -> str:
        proc = subprocess.run(args, capture_output=True, check=True, text=True)
        return proc.stdout.strip() or proc.stderr.strip()

    def set(self, key: str, value: dict) -> AnchorWriteResult:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        cmd = self._base() + ["safeset", key, payload]
        output = self._run(cmd)
        tx_id = None
        for line in output.splitlines():
            if "Tx:" in line:
                tx_id = line.split("Tx:", 1)[1].strip().split()[0]
                break
        return AnchorWriteResult(key=key, value=value, backend=self.backend, tx_id=tx_id)


class ImmudbPyAnchorClient:
    backend = "immudb_py"

    def __init__(self):
        from immudb import ImmudbClient  # type: ignore

        settings = get_settings()
        self.client = ImmudbClient(f"{settings.immudb_address}:{settings.immudb_port}")
        self.client.login(
            settings.immudb_user,
            settings.immudb_password,
            database=settings.immudb_database.encode("utf-8"),
        )

    def set(self, key: str, value: dict) -> AnchorWriteResult:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        resp = self.client.verifiedSet(key.encode("utf-8"), payload.encode("utf-8"))
        tx_id = None
        for attr in ("id", "tx", "Tx"):
            if hasattr(resp, attr):
                tx_id = str(getattr(resp, attr))
                break
        return AnchorWriteResult(key=key, value=value, backend=self.backend, tx_id=tx_id)


class AnchoringService:
    def __init__(self, session: Session, client: AnchorClient | None = None):
        self.session = session
        self.client = client or self._build_client()

    def _build_client(self) -> AnchorClient:
        settings = get_settings()
        mode = settings.anchor_mode
        if mode == "immudb_py":
            try:
                return ImmudbPyAnchorClient()
            except Exception as exc:
                if settings.anchor_strict:
                    raise RuntimeError(f"immudb_py unavailable in strict mode: {exc}") from exc
                logger.warning("immudb_py unavailable, falling back to fake: %s", exc)
        if mode == "immudb_cli":
            try:
                return ImmudbCliAnchorClient()
            except Exception as exc:
                if settings.anchor_strict:
                    raise RuntimeError(f"immudb_cli unavailable in strict mode: {exc}") from exc
                logger.warning("immudb_cli unavailable, falling back to fake: %s", exc)
        return FakeAnchorClient()

    def _persist(self, result: AnchorWriteResult) -> AnchorWriteResult:
        record = self.session.scalar(select(AnchorRecordModel).where(AnchorRecordModel.key == result.key))
        if record is None:
            record = AnchorRecordModel(
                key=result.key,
                value_json=result.value,
                backend=result.backend,
                tx_id=result.tx_id,
                created_at=datetime.now(timezone.utc),
            )
            self.session.add(record)
        else:
            record.value_json = result.value
            record.backend = result.backend
            record.tx_id = result.tx_id
        self.session.flush()
        return result

    def _safe_set(self, key: str, value: dict) -> AnchorWriteResult:
        try:
            result = self.client.set(key, value)
        except Exception as exc:
            if get_settings().anchor_strict and self.client.backend != "fake":
                raise RuntimeError(
                    f"anchor write failed on backend={self.client.backend} in strict mode"
                ) from exc
            logger.warning("anchor write failed on backend=%s, fallback to fake: %s", self.client.backend, exc)
            self.client = FakeAnchorClient()
            result = self.client.set(key, value)
        return self._persist(result)

    def anchor_disclosure(
        self,
        disclosure_id: str,
        policy_id: str,
        period: dict,
        root_summary: str,
        statement_sig_hash: str,
        root_details: str | None = None,
    ) -> dict:
        payload = {
            "disclosure_id": disclosure_id,
            "policy_id": policy_id,
            "period": period,
            "root_summary": root_summary,
            "root_details": root_details,
            "statement_sig_hash": statement_sig_hash,
            "anchored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        refs = {}
        refs["disclosure"] = self._safe_set(f"disclosure:{disclosure_id}", payload).__dict__
        refs["root_summary"] = self._safe_set(
            f"root:summary:{period['start']}:{policy_id}", {"root_summary": root_summary}
        ).__dict__
        if root_details:
            refs["root_details"] = self._safe_set(
                f"root:details:{period['start']}:{policy_id}", {"root_details": root_details}
            ).__dict__
        return refs

    def anchor_receipt(self, receipt_hash: str, object_key: str, source: str, occurred_at: str) -> dict:
        payload = {
            "receipt_hash": receipt_hash,
            "object_key": object_key,
            "source": source,
            "occurred_at": occurred_at,
            "anchored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        result = self._safe_set(f"receipt:{receipt_hash}", payload)
        return result.__dict__

    def get_disclosure_anchor(self, disclosure_id: str) -> dict | None:
        record = self.session.scalar(
            select(AnchorRecordModel).where(AnchorRecordModel.key == f"disclosure:{disclosure_id}")
        )
        if not record:
            return None
        return {
            "key": record.key,
            "value": record.value_json,
            "backend": record.backend,
            "tx_id": record.tx_id,
            "created_at": record.created_at.isoformat().replace("+00:00", "Z"),
        }
