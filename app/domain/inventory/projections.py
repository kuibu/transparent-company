from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.persistence.models import InventoryViewModel, LedgerEventModel


def _procurement_unit_cost(session: Session, procurement_id: str, sku: str) -> int:
    events = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "ProcurementOrdered")
            .order_by(LedgerEventModel.seq_id.desc())
        ).all()
    )
    for event in events:
        if event.payload.get("procurement_id") != procurement_id:
            continue
        for item in event.payload.get("items", []):
            if item.get("sku") == sku:
                return int(item.get("unit_cost", 0))
    return 0


def _upsert_batch(
    session: Session,
    sku: str,
    batch_id: str,
    qty_delta: int,
    expiry_date: str | None,
    unit_cost: int,
    occurred_at: datetime,
) -> InventoryViewModel:
    batch = session.scalar(
        select(InventoryViewModel)
        .where(InventoryViewModel.sku == sku)
        .where(InventoryViewModel.batch_id == batch_id)
    )
    if batch is None:
        batch = InventoryViewModel(
            sku=sku,
            batch_id=batch_id,
            qty_on_hand=0,
            expiry_date=expiry_date,
            unit_cost_cents=unit_cost,
            updated_at=occurred_at,
        )
        session.add(batch)
    batch.qty_on_hand += qty_delta
    batch.updated_at = occurred_at
    if expiry_date:
        batch.expiry_date = expiry_date
    if unit_cost:
        batch.unit_cost_cents = unit_cost
    if batch.qty_on_hand < 0:
        raise ValueError(f"negative inventory prevented for {sku}/{batch_id}")
    return batch


def _consume_fifo(session: Session, sku: str, qty: int, occurred_at: datetime) -> int:
    # Session autoflush is disabled globally, so we flush here to ensure
    # previously applied GoodsReceived rows are visible to this FIFO query.
    session.flush()
    rows = list(
        session.scalars(
            select(InventoryViewModel)
            .where(InventoryViewModel.sku == sku)
            .where(InventoryViewModel.qty_on_hand > 0)
            .order_by(InventoryViewModel.expiry_date.asc(), InventoryViewModel.batch_id.asc())
        ).all()
    )
    remaining = qty
    total_cost = 0
    for row in rows:
        if remaining <= 0:
            break
        take = min(row.qty_on_hand, remaining)
        row.qty_on_hand -= take
        row.updated_at = occurred_at
        remaining -= take
        total_cost += take * int(row.unit_cost_cents)
    if remaining > 0:
        raise ValueError(f"negative inventory prevented for sku={sku}")
    return total_cost


def consume_inventory_for_shipment(session: Session, shipment_items: list[dict], occurred_at: datetime) -> int:
    total_cost = 0
    for item in shipment_items:
        total_cost += _consume_fifo(session, item["sku"], int(item["qty"]), occurred_at)
    return total_cost


def apply_inventory_event(session: Session, event: LedgerEventModel) -> int:
    payload = event.payload
    occurred_at = event.occurred_at.astimezone(timezone.utc)
    if event.event_type == "GoodsReceived":
        if not payload.get("qc_passed", False):
            return 0
        procurement_id = payload.get("procurement_id", "")
        batch_id = payload["batch_id"]
        for item in payload.get("items", []):
            unit_cost = int(item.get("unit_cost") or _procurement_unit_cost(session, procurement_id, item["sku"]))
            _upsert_batch(
                session=session,
                sku=item["sku"],
                batch_id=batch_id,
                qty_delta=int(item["qty"]),
                expiry_date=item.get("expiry_date"),
                unit_cost=unit_cost,
                occurred_at=occurred_at,
            )
        return 0

    if event.event_type == "ShipmentDispatched":
        return consume_inventory_for_shipment(session, payload.get("items", []), occurred_at)

    if event.event_type == "InventoryAdjusted":
        for item in payload.get("items", []):
            batch_id = item.get("batch_id") or "adjustment"
            _upsert_batch(
                session=session,
                sku=item["sku"],
                batch_id=batch_id,
                qty_delta=int(item["qty_delta"]),
                expiry_date=None,
                unit_cost=0,
                occurred_at=occurred_at,
            )
        return 0

    return 0


def rebuild_inventory_view(session: Session, events: Iterable[LedgerEventModel]) -> dict[str, int]:
    session.execute(delete(InventoryViewModel))
    shipment_costs: dict[str, int] = {}
    for event in events:
        cost = apply_inventory_event(session, event)
        if event.event_type == "ShipmentDispatched":
            shipment_costs[event.event_id] = cost
    return shipment_costs
