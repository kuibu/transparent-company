from __future__ import annotations

from app.core.security import Actor
from app.ledger.events import EventCreateRequest


def order_procurement(
    actor: Actor,
    procurement_id: str,
    supplier_id: str,
    items: list[dict],
    expected_date: str,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="ProcurementOrdered",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "procurement_id": procurement_id,
            "supplier_id": supplier_id,
            "items": items,
            "expected_date": expected_date,
        },
    )


def receive_goods(
    actor: Actor,
    procurement_id: str,
    batch_id: str,
    items: list[dict],
    qc_passed: bool,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="GoodsReceived",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "procurement_id": procurement_id,
            "batch_id": batch_id,
            "items": items,
            "qc_passed": qc_passed,
        },
    )


def adjust_inventory(
    actor: Actor,
    reason: str,
    items: list[dict],
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="InventoryAdjusted",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={"reason": reason, "items": items},
    )
