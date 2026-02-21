from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.inventory.projections import rebuild_inventory_view
from app.domain.orders.projections import rebuild_orders_view
from app.persistence.models import LedgerEventModel


def rebuild_all_read_models(session: Session, events: list[LedgerEventModel] | None = None) -> dict[str, int]:
    if events is None:
        events = list(
            session.query(LedgerEventModel)
            .order_by(LedgerEventModel.seq_id.asc())
            .all()
        )
    rebuild_orders_view(session, events)
    shipment_costs = rebuild_inventory_view(session, events)
    return shipment_costs
