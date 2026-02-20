from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.disclosure.commitment import build_commitments
from app.disclosure.compute import compute_disclosure
from app.disclosure.policies import get_policy
from app.domain.accounting.reports import generate_pnl
from app.domain.projections import rebuild_all_read_models
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.models import LedgerEventModel


def _append(session, event_type: str, payload: dict, ts: datetime):
    req = EventCreateRequest(
        event_type=event_type,
        actor={"type": "agent", "id": "agent-test"},
        payload=payload,
        occurred_at=ts,
    )
    return LedgerStore(session).append(req, signer=load_role_key("agent"))


def test_replay_consistency(session):
    base = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    _append(
        session,
        "ProcurementOrdered",
        {
            "procurement_id": "P1",
            "supplier_id": "S1",
            "items": [{"sku": "tomato", "qty": 100, "unit_cost": 200}],
            "expected_date": "2026-01-11",
        },
        base,
    )
    _append(
        session,
        "GoodsReceived",
        {
            "procurement_id": "P1",
            "batch_id": "B1",
            "items": [{"sku": "tomato", "qty": 100, "expiry_date": "2026-01-20", "unit_cost": 200}],
            "qc_passed": True,
        },
        base + timedelta(hours=1),
    )
    _append(
        session,
        "OrderPlaced",
        {
            "order_id": "O1",
            "customer_ref": "C1",
            "items": [{"sku": "tomato", "qty": 10, "unit_price": 500}],
            "channel": "online",
            "region": "east",
        },
        base + timedelta(hours=2),
    )
    _append(
        session,
        "PaymentCaptured",
        {
            "order_id": "O1",
            "amount": 5000,
            "method": "card",
            "receipt_object_key": "r1",
            "receipt_hash": "h1",
        },
        base + timedelta(hours=3),
    )
    _append(
        session,
        "ShipmentDispatched",
        {"order_id": "O1", "items": [{"sku": "tomato", "qty": 10}], "carrier_ref": "CARRIER"},
        base + timedelta(hours=4),
    )

    events = list(session.query(LedgerEventModel).order_by(LedgerEventModel.seq_id.asc()).all())

    shipment_costs_1 = rebuild_all_read_models(session)
    pnl_1 = generate_pnl(events, shipment_costs=shipment_costs_1)
    policy = get_policy("policy_public_v1")
    comp_1 = compute_disclosure(
        events=events,
        policy=policy,
        period_start=base,
        period_end=base + timedelta(days=1),
        group_by=["channel"],
        pnl_report=pnl_1,
    )
    commitments_1 = build_commitments(
        metrics=comp_1.metrics,
        grouped_metrics=comp_1.grouped_metrics,
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash(),
        period={"start": base.isoformat().replace("+00:00", "Z"), "end": (base + timedelta(days=1)).isoformat().replace("+00:00", "Z")},
        proof_level=policy.proof_level,
        detail_event_map=comp_1.detail_event_map,
    )

    shipment_costs_2 = rebuild_all_read_models(session)
    pnl_2 = generate_pnl(events, shipment_costs=shipment_costs_2)
    comp_2 = compute_disclosure(
        events=events,
        policy=policy,
        period_start=base,
        period_end=base + timedelta(days=1),
        group_by=["channel"],
        pnl_report=pnl_2,
    )
    commitments_2 = build_commitments(
        metrics=comp_2.metrics,
        grouped_metrics=comp_2.grouped_metrics,
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash(),
        period={"start": base.isoformat().replace("+00:00", "Z"), "end": (base + timedelta(days=1)).isoformat().replace("+00:00", "Z")},
        proof_level=policy.proof_level,
        detail_event_map=comp_2.detail_event_map,
    )

    assert pnl_1 == pnl_2
    assert comp_1.metrics == comp_2.metrics
    assert commitments_1.root_summary == commitments_2.root_summary
