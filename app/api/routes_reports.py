from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.utils import parse_period
from app.domain.accounting.reports import generate_pnl
from app.domain.projections import rebuild_all_read_models
from app.persistence.models import LedgerEventModel
from app.persistence.pg import get_session

router = APIRouter(tags=["reports"])


@router.get("/reports/pnl")
def get_pnl(
    period: str = Query(..., description="ISO period: start/end"),
    session: Session = Depends(get_session),
):
    try:
        start, end = parse_period(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    shipment_costs = rebuild_all_read_models(session)
    events = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.occurred_at >= start)
            .where(LedgerEventModel.occurred_at < end)
            .order_by(LedgerEventModel.seq_id.asc())
        ).all()
    )
    period_costs = {event_id: cost for event_id, cost in shipment_costs.items() if event_id in {e.event_id for e in events}}
    report = generate_pnl(events, shipment_costs=period_costs)
    return {
        "period": {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")},
        "report": report,
    }
