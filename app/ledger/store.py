from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from app.core.key_management import assert_signer_matches_actor
from app.governance import PolicyEnforcementError, get_governance_engine
from app.ledger.canonical import sha256_hex
from app.ledger.events import EventCreateRequest, LedgerEvent
from app.ledger.signing import KeyMaterial, sign_object
from app.persistence.models import LedgerEventModel


@dataclass
class EventSourcingPostgresConfig:
    persistence_module: str = "eventsourcing.postgres"
    table_name: str = "ledger_events"

    def as_env(self, db_url: str) -> dict[str, str]:
        # This structure mirrors eventsourcing's environment keys so the project
        # can migrate to native eventsourcing aggregates without changing infra.
        return {
            "PERSISTENCE_MODULE": self.persistence_module,
            "POSTGRES_DBNAME": db_url.rsplit("/", 1)[-1],
        }


class LedgerStore:
    def __init__(self, session: Session):
        self.session = session

    def _latest_event_hash(self) -> str:
        stmt = select(LedgerEventModel.event_hash).order_by(desc(LedgerEventModel.seq_id)).limit(1)
        latest = self.session.scalar(stmt)
        return latest or ("0" * 64)

    def _event_hash_input(self, event: LedgerEvent) -> dict:
        return {
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "actor": event.actor.model_dump(),
            "policy_id": event.policy_id,
            "payload": event.payload,
            "tool_trace": event.tool_trace,
            "prev_hash": event.prev_hash,
            "signature": event.signature,
        }

    def append(self, request: EventCreateRequest, signer: KeyMaterial) -> LedgerEventModel:
        try:
            assert_signer_matches_actor(request.actor.type, signer.key_id)
        except ValueError as exc:
            raise PolicyEnforcementError(str(exc)) from exc

        governance = get_governance_engine()
        raw_approvals = (request.tool_trace or {}).get("approvals", [])
        approvals = [str(x) for x in raw_approvals] if isinstance(raw_approvals, list) else []
        decision = governance.evaluate(
            action=f"event:{request.event_type}",
            actor_type=request.actor.type,
            signer_role=signer.key_id,
            payload=request.payload,
            tool_trace=request.tool_trace,
            approvals=approvals,
        )
        if not decision.allowed:
            raise PolicyEnforcementError(decision.reason)

        prev_hash = self._latest_event_hash()
        event = request.to_ledger_event(prev_hash=prev_hash)

        # Persist governance decision with each event for replayable rule audits.
        event_tool_trace = dict(event.tool_trace)
        event_tool_trace["governance"] = decision.to_audit_dict()
        event.tool_trace = event_tool_trace

        sign_payload = {
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "actor": event.actor.model_dump(),
            "policy_id": event.policy_id,
            "payload": event.payload,
            "tool_trace": event.tool_trace,
            "prev_hash": event.prev_hash,
        }
        event.signature = sign_object(sign_payload, signer)
        event_hash = sha256_hex(self._event_hash_input(event))

        row = LedgerEventModel(
            event_id=str(event.event_id),
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            actor_type=event.actor.type,
            actor_id=event.actor.id,
            policy_id=event.policy_id,
            payload=event.payload,
            tool_trace=event.tool_trace,
            prev_hash=event.prev_hash,
            event_hash=event_hash,
            signature=event.signature,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        event_types: Iterable[str] | None = None,
    ) -> list[LedgerEventModel]:
        stmt: Select[tuple[LedgerEventModel]] = select(LedgerEventModel).order_by(LedgerEventModel.seq_id.asc())
        if start is not None:
            stmt = stmt.where(LedgerEventModel.occurred_at >= start)
        if end is not None:
            stmt = stmt.where(LedgerEventModel.occurred_at < end)
        if event_types:
            stmt = stmt.where(LedgerEventModel.event_type.in_(list(event_types)))
        return list(self.session.scalars(stmt).all())

    def get_event_by_id(self, event_id: str) -> LedgerEventModel | None:
        stmt = select(LedgerEventModel).where(LedgerEventModel.event_id == event_id)
        return self.session.scalar(stmt)

    def verify_chain(self) -> bool:
        events = self.list_events()
        prev = "0" * 64
        for event in events:
            if event.prev_hash != prev:
                return False
            raw = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "actor": {"type": event.actor_type, "id": event.actor_id},
                "policy_id": event.policy_id,
                "payload": event.payload,
                "tool_trace": event.tool_trace,
                "prev_hash": event.prev_hash,
                "signature": event.signature,
            }
            expected = sha256_hex(raw)
            if expected != event.event_hash:
                return False
            prev = event.event_hash
        return True
