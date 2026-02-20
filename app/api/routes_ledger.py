from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.security import Actor, get_actor
from app.ledger.anchoring import AnchoringService
from app.persistence.models import LedgerEventModel
from app.persistence.pg import get_session

router = APIRouter(tags=["ledger"])


def _require_internal_full_ledger(actor: Actor) -> None:
    if actor.type not in {"human", "auditor"}:
        raise HTTPException(status_code=403, detail="full ledger requires human/auditor role")


@router.get("/ledger/full/events")
def list_full_events(
    limit: int = Query(default=100, ge=1, le=1000),
    event_type: str | None = Query(default=None),
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    _require_internal_full_ledger(actor)

    stmt = select(LedgerEventModel).order_by(desc(LedgerEventModel.seq_id)).limit(limit)
    if event_type:
        stmt = stmt.where(LedgerEventModel.event_type == event_type)

    rows = list(session.scalars(stmt).all())
    return {
        "ledger": "full",
        "count": len(rows),
        "events": [
            {
                "seq_id": row.seq_id,
                "event_id": row.event_id,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "actor": {"type": row.actor_type, "id": row.actor_id},
                "policy_id": row.policy_id,
                "payload": row.payload,
                "tool_trace": row.tool_trace,
                "prev_hash": row.prev_hash,
                "event_hash": row.event_hash,
                "signature": row.signature,
            }
            for row in rows
        ],
    }


@router.get("/ledger/public/events")
def list_public_events(
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    stmt = (
        select(LedgerEventModel)
        .where(LedgerEventModel.event_type == "DisclosurePublished")
        .order_by(desc(LedgerEventModel.seq_id))
        .limit(limit)
    )
    rows = list(session.scalars(stmt).all())

    items = []
    for row in rows:
        payload = row.payload or {}
        items.append(
            {
                "event_id": row.event_id,
                "event_hash": row.event_hash,
                "prev_hash": row.prev_hash,
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "commitment": {
                    "disclosure_id": payload.get("disclosure_id"),
                    "policy_id": payload.get("policy_id"),
                    "period": payload.get("period"),
                    "metrics": payload.get("metrics"),
                    "root_summary": payload.get("merkle_root"),
                    "anchor_ref": payload.get("anchor_ref"),
                    "statement_sig_hash": payload.get("statement_sig_hash"),
                },
            }
        )

    return {
        "ledger": "public",
        "count": len(items),
        "events": items,
        "note": "Public ledger exposes commitments and aggregate disclosure only; no raw internal details.",
    }


@router.get("/anchor/disclosure/{disclosure_id}")
def get_disclosure_anchor(disclosure_id: str, session: Session = Depends(get_session)):
    anchor = AnchoringService(session).get_disclosure_anchor(disclosure_id)
    if not anchor:
        raise HTTPException(status_code=404, detail="anchor not found")
    return anchor
