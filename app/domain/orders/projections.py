from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.persistence.models import LedgerEventModel, OrderViewModel


def _upsert_order(session: Session, order_id: str, occurred_at: datetime) -> OrderViewModel:
    # Session autoflush is disabled globally, so flush pending inserts to
    # guarantee primary-key lookups can find rows created earlier in replay.
    session.flush()
    order = session.scalar(select(OrderViewModel).where(OrderViewModel.order_id == order_id))
    if order is None:
        order = OrderViewModel(
            order_id=order_id,
            customer_ref=None,
            channel=None,
            region=None,
            status="placed",
            order_total_cents=0,
            paid_cents=0,
            refunded_cents=0,
            shipped_qty=0,
            line_items=[],
            updated_at=occurred_at,
        )
        session.add(order)
    return order


def apply_order_event(session: Session, event: LedgerEventModel) -> None:
    payload = event.payload
    now = event.occurred_at.astimezone(timezone.utc)

    if event.event_type == "OrderPlaced":
        order_id = payload["order_id"]
        order = _upsert_order(session, order_id, now)
        order.customer_ref = payload.get("customer_ref")
        order.channel = payload.get("channel")
        order.region = payload.get("region")
        order.line_items = payload.get("items", [])
        order.order_total_cents = sum(
            int(item["qty"]) * int(item["unit_price"]) for item in payload.get("items", [])
        )
        order.status = "placed"
        order.updated_at = now
        return

    if event.event_type == "PaymentCaptured":
        order = _upsert_order(session, payload["order_id"], now)
        order.paid_cents += int(payload["amount"])
        if order.paid_cents > 0:
            order.status = "paid"
        order.updated_at = now
        return

    if event.event_type == "ShipmentDispatched":
        order = _upsert_order(session, payload["order_id"], now)
        order.shipped_qty += sum(int(i["qty"]) for i in payload.get("items", []))
        order.status = "shipped"
        order.updated_at = now
        return

    if event.event_type == "RefundIssued":
        order = _upsert_order(session, payload["order_id"], now)
        order.refunded_cents += int(payload["amount"])
        if order.refunded_cents > 0:
            order.status = "refunded"
        order.updated_at = now


def rebuild_orders_view(session: Session, events: Iterable[LedgerEventModel]) -> None:
    session.execute(delete(OrderViewModel))
    for event in events:
        apply_order_event(session, event)
