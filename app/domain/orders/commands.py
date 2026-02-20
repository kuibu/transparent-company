from __future__ import annotations

from app.core.security import Actor
from app.ledger.events import EventCreateRequest


def place_order(
    actor: Actor,
    order_id: str,
    customer_ref: str,
    items: list[dict],
    channel: str,
    region: str | None,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="OrderPlaced",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "order_id": order_id,
            "customer_ref": customer_ref,
            "items": items,
            "channel": channel,
            "region": region,
        },
    )


def capture_payment(
    actor: Actor,
    order_id: str,
    amount: int,
    method: str,
    receipt_object_key: str,
    receipt_hash: str,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="PaymentCaptured",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "order_id": order_id,
            "amount": amount,
            "method": method,
            "receipt_object_key": receipt_object_key,
            "receipt_hash": receipt_hash,
        },
    )


def dispatch_shipment(
    actor: Actor,
    order_id: str,
    items: list[dict],
    carrier_ref: str,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="ShipmentDispatched",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "order_id": order_id,
            "items": items,
            "carrier_ref": carrier_ref,
        },
    )


def issue_refund(
    actor: Actor,
    order_id: str,
    amount: int,
    receipt_hash: str,
    policy_id: str = "policy_internal_v1",
) -> EventCreateRequest:
    return EventCreateRequest(
        event_type="RefundIssued",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload={
            "order_id": order_id,
            "amount": amount,
            "receipt_hash": receipt_hash,
        },
    )
