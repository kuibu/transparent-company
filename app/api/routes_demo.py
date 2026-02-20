from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import Actor
from app.disclosure.publisher import publish_disclosure_run
from app.ledger.anchoring import AnchoringService
from app.ledger.events import EventCreateRequest
from app.ledger.receipts import build_receipt_store
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.pg import get_session

router = APIRouter(tags=["demo"])


def _append(
    session: Session,
    signer_role: str,
    actor: Actor,
    event_type: str,
    payload: dict,
    occurred_at: datetime,
    policy_id: str = "policy_internal_v1",
    tool_trace: dict | None = None,
):
    req = EventCreateRequest(
        event_type=event_type,
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload=payload,
        tool_trace=tool_trace or {},
        occurred_at=occurred_at,
    )
    return LedgerStore(session).append(req, signer=load_role_key(signer_role))


@router.post("/demo/seed")
def demo_seed(session: Session = Depends(get_session)):
    agent = Actor(type="agent", id="agent-ops-001")
    receipt_store = build_receipt_store()
    anchor_service = AnchoringService(session)

    base_day = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    t0 = datetime.combine(base_day, time(8, 0), tzinfo=timezone.utc)

    procurement_id = f"PROC-{uuid4().hex[:8]}"
    order_id = f"ORD-{uuid4().hex[:8]}"
    batch_id = f"BATCH-{uuid4().hex[:8]}"

    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="ProcurementOrdered",
        occurred_at=t0,
        payload={
            "procurement_id": procurement_id,
            "supplier_id": "supplier-greenfarm",
            "items": [{"sku": "tomato", "qty": 100, "unit_cost": 320}],
            "expected_date": (base_day + timedelta(days=1)).isoformat(),
        },
    )

    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="GoodsReceived",
        occurred_at=t0 + timedelta(hours=1),
        payload={
            "procurement_id": procurement_id,
            "batch_id": batch_id,
            "items": [{"sku": "tomato", "qty": 100, "expiry_date": (base_day + timedelta(days=7)).isoformat(), "unit_cost": 320}],
            "qc_passed": True,
        },
    )

    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="OrderPlaced",
        occurred_at=t0 + timedelta(hours=2),
        payload={
            "order_id": order_id,
            "customer_ref": "cust-public-demo",
            "items": [{"sku": "tomato", "qty": 30, "unit_price": 550}],
            "channel": "online",
            "region": "east",
        },
    )

    receipt_object_key = f"payments/{order_id}.json"
    receipt = receipt_store.put_json(
        object_key=receipt_object_key,
        payload={
            "order_id": order_id,
            "amount": 16500,
            "paid_at": (t0 + timedelta(hours=2, minutes=10)).isoformat().replace("+00:00", "Z"),
            "provider": "sandbox-pay",
        },
    )

    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="PaymentCaptured",
        occurred_at=t0 + timedelta(hours=2, minutes=10),
        payload={
            "order_id": order_id,
            "amount": 16500,
            "method": "card",
            "receipt_object_key": receipt.object_key,
            "receipt_hash": receipt.receipt_hash,
        },
    )

    receipt_anchor = anchor_service.anchor_receipt(
        receipt_hash=receipt.receipt_hash,
        object_key=receipt.object_key,
        source=receipt.backend,
        occurred_at=(t0 + timedelta(hours=2, minutes=10)).isoformat().replace("+00:00", "Z"),
    )

    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="ShipmentDispatched",
        occurred_at=t0 + timedelta(hours=3),
        payload={
            "order_id": order_id,
            "items": [{"sku": "tomato", "qty": 30}],
            "carrier_ref": "carrier-demo-001",
        },
    )

    # One partial refund to demonstrate contra-flow.
    _append(
        session,
        signer_role="agent",
        actor=agent,
        event_type="RefundIssued",
        occurred_at=t0 + timedelta(hours=4),
        payload={
            "order_id": order_id,
            "amount": 500,
            "receipt_hash": "refund-demo-hash-001",
        },
    )

    period_start = datetime.combine(base_day, time(0, 0), tzinfo=timezone.utc)
    period_end = period_start + timedelta(days=1)

    public_result = publish_disclosure_run(
        session=session,
        policy_id="policy_public_v1",
        period_start=period_start,
        period_end=period_end,
        group_by=["channel"],
        actor=agent,
    )
    investor_result = publish_disclosure_run(
        session=session,
        policy_id="policy_investor_v1",
        period_start=period_start,
        period_end=period_end,
        group_by=["channel", "sku"],
        actor=agent,
    )

    return {
        "scenario": "procure_receive_order_pay_ship_refund_publish",
        "period": {
            "start": period_start.isoformat().replace("+00:00", "Z"),
            "end": period_end.isoformat().replace("+00:00", "Z"),
        },
        "receipt": {
            "object_key": receipt.object_key,
            "receipt_hash": receipt.receipt_hash,
            "anchor": receipt_anchor,
        },
        "public_disclosure": {
            "disclosure_id": public_result.disclosure_id,
            "root_summary": public_result.payload["root_summary"],
            "root_details": public_result.payload["root_details"],
            "agent_public_key": public_result.payload["agent_public_key"],
        },
        "investor_disclosure": {
            "disclosure_id": investor_result.disclosure_id,
            "root_summary": investor_result.payload["root_summary"],
            "root_details": investor_result.payload["root_details"],
            "agent_public_key": investor_result.payload["agent_public_key"],
        },
        "how_to_verify": {
            "proof_api": f"/disclosure/{public_result.disclosure_id}/proof?metric_key=revenue_cents",
            "anchor_api": f"/anchor/disclosure/{public_result.disclosure_id}",
            "script": "python scripts/verify_disclosure.py --base-url http://localhost:8000 --disclosure-id <id> --metric-key revenue_cents",
        },
        "superset": {
            "url": "http://localhost:8088",
            "username": "admin",
            "password": "admin",
            "datasets": ["disclosure_metrics", "disclosure_grouped_metrics"],
        },
    }
