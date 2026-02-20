from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.connectors import get_connector
from app.core.key_management import expected_signer_role
from app.core.security import Actor
from app.disclosure.publisher import publish_disclosure_run
from app.ledger.anchoring import AnchoringService
from app.ledger.events import EventCreateRequest
from app.ledger.receipts import build_receipt_store
from app.ledger.signing import load_role_key, verify_object
from app.ledger.store import LedgerStore
from app.persistence.models import LedgerEventModel


DEFAULT_SCENARIO_ID = "default_transparent_company_story_v1"
DEFAULT_SCENARIO_VERSION = "1.0.0"


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


def _append(
    session: Session,
    actor: Actor,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    policy_id: str = "policy_internal_v1",
    tool_trace: dict[str, Any] | None = None,
):
    signer_role = expected_signer_role(actor.type)
    req = EventCreateRequest(
        event_type=event_type,
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy_id,
        payload=payload,
        tool_trace=tool_trace or {},
        occurred_at=occurred_at,
    )
    return LedgerStore(session).append(req, signer=load_role_key(signer_role))


def _marker_event(session: Session) -> LedgerEventModel | None:
    rows = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "DemoScenarioInitialized")
            .order_by(desc(LedgerEventModel.seq_id))
        ).all()
    )
    for row in rows:
        payload = row.payload or {}
        if payload.get("scenario_id") == DEFAULT_SCENARIO_ID:
            return row
    return None


def _find_disclosure_publish_event(session: Session, disclosure_id: str) -> LedgerEventModel | None:
    rows = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type == "DisclosurePublished")
            .order_by(desc(LedgerEventModel.seq_id))
        ).all()
    )
    for row in rows:
        payload = row.payload or {}
        if payload.get("disclosure_id") == disclosure_id:
            return row
    return None


def _verify_event_signature(row: LedgerEventModel) -> bool:
    sign_payload = {
        "event_id": row.event_id,
        "event_type": row.event_type,
        "occurred_at": row.occurred_at,
        "actor": {"type": row.actor_type, "id": row.actor_id},
        "policy_id": row.policy_id,
        "payload": row.payload,
        "tool_trace": row.tool_trace,
        "prev_hash": row.prev_hash,
    }
    pub = load_role_key(expected_signer_role(row.actor_type)).public_key_b64
    try:
        return verify_object(sign_payload, row.signature, pub)
    except Exception:
        return False


def _identity_report(session: Session, event_ids: list[str]) -> dict[str, Any]:
    if not event_ids:
        return {
            "checked_event_count": 0,
            "valid_signature_count": 0,
            "all_valid": True,
            "samples": [],
        }

    rows = list(
        session.scalars(select(LedgerEventModel).where(LedgerEventModel.event_id.in_(event_ids))).all()
    )
    by_id = {row.event_id: row for row in rows}

    samples = []
    valid = 0
    total = 0
    for event_id in event_ids:
        row = by_id.get(event_id)
        if row is None:
            continue
        ok = _verify_event_signature(row)
        total += 1
        valid += 1 if ok else 0
        samples.append(
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "actor": {"type": row.actor_type, "id": row.actor_id},
                "signature_valid": ok,
            }
        )

    return {
        "checked_event_count": total,
        "valid_signature_count": valid,
        "all_valid": total == valid,
        "samples": samples,
    }


def _log_tool_invocation(
    session: Session,
    actor: Actor,
    connector_name: str,
    action: str,
    payload: dict[str, Any],
    run_id: str,
    task_id: str,
    occurred_at: datetime,
) -> LedgerEventModel:
    signer_role = expected_signer_role(actor.type)
    connector = get_connector(connector_name)
    result = connector.invoke(
        action=action,
        payload=payload,
        actor_type=actor.type,
        signer_role=signer_role,
        approvals=[actor.type],
    )

    event_payload = {
        "run_id": run_id,
        "task_id": task_id,
        "connector": connector_name,
        "action": action,
        "status": "success",
        "attempt": 1,
        "timeout_seconds": 30,
        "max_retries": 0,
        "request_hash": result.request_hash,
        "response_hash": result.response_hash,
        "error": None,
        "governance": result.governance,
    }
    tool_trace = {
        "scenario_id": DEFAULT_SCENARIO_ID,
        "connector": connector_name,
        "action": action,
        "response_receipt_hash": result.receipt_hash,
    }
    return _append(
        session=session,
        actor=actor,
        event_type="ToolInvocationLogged",
        payload=event_payload,
        occurred_at=occurred_at,
        tool_trace=tool_trace,
    )


def _capture_payment(
    session: Session,
    anchor_service: AnchoringService,
    receipt_store,
    actor: Actor,
    order_id: str,
    amount: int,
    paid_at: datetime,
    method: str = "card",
):
    object_key = f"payments/{order_id}-{paid_at.strftime('%Y%m%d%H%M')}.json"
    receipt = receipt_store.put_json(
        object_key=object_key,
        payload={
            "order_id": order_id,
            "amount": amount,
            "paid_at": paid_at.isoformat().replace("+00:00", "Z"),
            "provider": "default-demo-pay",
        },
    )

    row = _append(
        session=session,
        actor=actor,
        event_type="PaymentCaptured",
        occurred_at=paid_at,
        payload={
            "order_id": order_id,
            "amount": amount,
            "method": method,
            "receipt_object_key": receipt.object_key,
            "receipt_hash": receipt.receipt_hash,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )

    receipt_anchor = anchor_service.anchor_receipt(
        receipt_hash=receipt.receipt_hash,
        object_key=receipt.object_key,
        source=receipt.backend,
        occurred_at=paid_at.isoformat().replace("+00:00", "Z"),
    )
    return row, receipt, receipt_anchor


def _minimal_disclosure(disclosure_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "disclosure_id": disclosure_payload["disclosure_id"],
        "policy_id": disclosure_payload["policy_id"],
        "root_summary": disclosure_payload["root_summary"],
        "root_details": disclosure_payload["root_details"],
        "signer_role": disclosure_payload.get("signer_role"),
        "signer_public_key": disclosure_payload.get("signer_public_key"),
        "agent_public_key": disclosure_payload.get("agent_public_key"),
    }


def _build_existing_response(session: Session, marker: LedgerEventModel) -> dict[str, Any]:
    payload = deepcopy(marker.payload or {})
    result = deepcopy(payload.get("result", {}))
    key_event_ids = payload.get("key_event_ids", [])
    result["seeded_now"] = False
    result["identity_proof"] = _identity_report(session, key_event_ids)
    return result


def seed_default_scenario(session: Session) -> dict[str, Any]:
    marker = _marker_event(session)
    if marker is not None:
        return _build_existing_response(session, marker)

    agent = Actor(type="agent", id="agent-chief-001")
    human = Actor(type="human", id="human-cfo-001")
    auditor = Actor(type="auditor", id="auditor-external-001")
    system = Actor(type="system", id="system-bootstrap-001")

    receipt_store = build_receipt_store()
    anchor_service = AnchoringService(session)

    base_day = (datetime.now(timezone.utc) - timedelta(days=4)).date()
    day1 = base_day
    day2 = base_day + timedelta(days=1)

    period1_start = _dt(day1, 0)
    period1_end = period1_start + timedelta(days=1)
    period2_start = _dt(day2, 0)
    period2_end = period2_start + timedelta(days=1)

    key_rows: list[LedgerEventModel] = []
    action_log: list[dict[str, Any]] = []
    receipt_anchors: list[dict[str, Any]] = []

    def remember(row: LedgerEventModel, meaning: str) -> None:
        key_rows.append(row)
        action_log.append(
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "actor": {"type": row.actor_type, "id": row.actor_id},
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "meaning": meaning,
            }
        )

    # Day 1: agent handles operations, human handles high-risk purchase and bank transfer.
    r = _log_tool_invocation(
        session,
        actor=agent,
        connector_name="supplier",
        action="place_order",
        payload={"supplier_id": "supplier-farm-a", "items": [{"sku": "tomato", "qty": 500}]},
        run_id="run-default-v1-day1",
        task_id="task-agent-place-order",
        occurred_at=_dt(day1, 7, 20),
    )
    remember(r, "Agent placed supplier order using operational tool connector")

    r = _append(
        session,
        actor=agent,
        event_type="ProcurementOrdered",
        occurred_at=_dt(day1, 8, 0),
        payload={
            "procurement_id": "PROC-DFT-A-001",
            "supplier_id": "supplier-farm-a",
            "items": [{"sku": "tomato", "qty": 500, "unit_cost": 300}],
            "expected_date": day1.isoformat(),
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent created routine procurement order")

    r = _append(
        session,
        actor=agent,
        event_type="ProcurementOrdered",
        occurred_at=_dt(day1, 8, 20),
        payload={
            "procurement_id": "PROC-DFT-A-002",
            "supplier_id": "supplier-farm-b",
            "items": [{"sku": "cucumber", "qty": 300, "unit_cost": 260}],
            "expected_date": day1.isoformat(),
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent created second routine procurement order")

    r = _append(
        session,
        actor=human,
        event_type="ProcurementOrdered",
        occurred_at=_dt(day1, 8, 40),
        payload={
            "procurement_id": "PROC-DFT-H-9000",
            "supplier_id": "supplier-strategic-c",
            "items": [{"sku": "orange", "qty": 1000, "unit_cost": 700}],
            "expected_date": day1.isoformat(),
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "approvals": ["human"]},
    )
    remember(r, "Human approved and signed high-value procurement above policy threshold")

    r = _append(
        session,
        actor=agent,
        event_type="GoodsReceived",
        occurred_at=_dt(day1, 9, 10),
        payload={
            "procurement_id": "PROC-DFT-A-001",
            "batch_id": "BATCH-TOMATO-001",
            "items": [{"sku": "tomato", "qty": 500, "expiry_date": (day1 + timedelta(days=7)).isoformat(), "unit_cost": 300}],
            "qc_passed": True,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent confirmed tomato inbound and QC pass")

    r = _append(
        session,
        actor=agent,
        event_type="GoodsReceived",
        occurred_at=_dt(day1, 9, 20),
        payload={
            "procurement_id": "PROC-DFT-A-002",
            "batch_id": "BATCH-CUCUMBER-001",
            "items": [{"sku": "cucumber", "qty": 300, "expiry_date": (day1 + timedelta(days=6)).isoformat(), "unit_cost": 260}],
            "qc_passed": True,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent confirmed cucumber inbound and QC pass")

    r = _append(
        session,
        actor=agent,
        event_type="GoodsReceived",
        occurred_at=_dt(day1, 9, 40),
        payload={
            "procurement_id": "PROC-DFT-H-9000",
            "batch_id": "BATCH-ORANGE-001",
            "items": [{"sku": "orange", "qty": 1000, "expiry_date": (day1 + timedelta(days=10)).isoformat(), "unit_cost": 700}],
            "qc_passed": True,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent received high-value lot after human approval")

    r = _append(
        session,
        actor=agent,
        event_type="OrderPlaced",
        occurred_at=_dt(day1, 11, 0),
        payload={
            "order_id": "ORD-DFT-1001",
            "customer_ref": "cust-demo-online-east",
            "items": [{"sku": "tomato", "qty": 80, "unit_price": 620}],
            "channel": "online",
            "region": "east",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent accepted online order in east region")

    r_pay, _, anchor = _capture_payment(
        session,
        anchor_service,
        receipt_store,
        actor=agent,
        order_id="ORD-DFT-1001",
        amount=49600,
        paid_at=_dt(day1, 11, 5),
    )
    remember(r_pay, "Agent captured payment and anchored receipt hash")
    receipt_anchors.append(anchor)

    r = _append(
        session,
        actor=agent,
        event_type="ShipmentDispatched",
        occurred_at=_dt(day1, 11, 30),
        payload={"order_id": "ORD-DFT-1001", "items": [{"sku": "tomato", "qty": 80}], "carrier_ref": "carrier-demo-east"},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent dispatched shipment for first order")

    r = _append(
        session,
        actor=agent,
        event_type="OrderPlaced",
        occurred_at=_dt(day1, 12, 0),
        payload={
            "order_id": "ORD-DFT-1002",
            "customer_ref": "cust-demo-retail-west",
            "items": [{"sku": "cucumber", "qty": 60, "unit_price": 500}],
            "channel": "retail",
            "region": "west",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent accepted retail order in west region")

    r_pay, _, anchor = _capture_payment(
        session,
        anchor_service,
        receipt_store,
        actor=agent,
        order_id="ORD-DFT-1002",
        amount=30000,
        paid_at=_dt(day1, 12, 10),
    )
    remember(r_pay, "Agent captured second payment and anchored receipt hash")
    receipt_anchors.append(anchor)

    r = _append(
        session,
        actor=agent,
        event_type="ShipmentDispatched",
        occurred_at=_dt(day1, 12, 40),
        payload={"order_id": "ORD-DFT-1002", "items": [{"sku": "cucumber", "qty": 60}], "carrier_ref": "carrier-demo-west"},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent dispatched second shipment")

    r = _append(
        session,
        actor=agent,
        event_type="RefundIssued",
        occurred_at=_dt(day1, 13, 20),
        payload={"order_id": "ORD-DFT-1002", "amount": 800, "receipt_hash": sha256(b"refund-ord-dft-1002").hexdigest()},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent handled customer refund")

    r = _log_tool_invocation(
        session,
        actor=human,
        connector_name="payment",
        action="bank_transfer",
        payload={"amount": 550000, "to": "supplier-strategic-c"},
        run_id="run-default-v1-day1",
        task_id="task-human-bank-transfer",
        occurred_at=_dt(day1, 14, 0),
    )
    remember(r, "Human executed high-risk bank transfer with governance approval")

    # Day 2: agent continues operations with different channels and SKU mix.
    r = _append(
        session,
        actor=agent,
        event_type="OrderPlaced",
        occurred_at=_dt(day2, 10, 0),
        payload={
            "order_id": "ORD-DFT-2001",
            "customer_ref": "cust-demo-online-north",
            "items": [{"sku": "orange", "qty": 120, "unit_price": 750}],
            "channel": "online",
            "region": "north",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent accepted day-2 online order for orange")

    r_pay, _, anchor = _capture_payment(
        session,
        anchor_service,
        receipt_store,
        actor=agent,
        order_id="ORD-DFT-2001",
        amount=90000,
        paid_at=_dt(day2, 10, 5),
    )
    remember(r_pay, "Agent captured day-2 payment and anchored receipt")
    receipt_anchors.append(anchor)

    r = _append(
        session,
        actor=agent,
        event_type="ShipmentDispatched",
        occurred_at=_dt(day2, 10, 40),
        payload={"order_id": "ORD-DFT-2001", "items": [{"sku": "orange", "qty": 120}], "carrier_ref": "carrier-demo-north"},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent dispatched orange shipment")

    r = _append(
        session,
        actor=agent,
        event_type="OrderPlaced",
        occurred_at=_dt(day2, 11, 0),
        payload={
            "order_id": "ORD-DFT-2002",
            "customer_ref": "cust-demo-wholesale-south",
            "items": [{"sku": "tomato", "qty": 90, "unit_price": 610}],
            "channel": "wholesale",
            "region": "south",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent accepted day-2 wholesale order")

    r_pay, _, anchor = _capture_payment(
        session,
        anchor_service,
        receipt_store,
        actor=agent,
        order_id="ORD-DFT-2002",
        amount=54900,
        paid_at=_dt(day2, 11, 6),
    )
    remember(r_pay, "Agent captured wholesale payment and anchored receipt")
    receipt_anchors.append(anchor)

    r = _append(
        session,
        actor=agent,
        event_type="ShipmentDispatched",
        occurred_at=_dt(day2, 11, 45),
        payload={"order_id": "ORD-DFT-2002", "items": [{"sku": "tomato", "qty": 90}], "carrier_ref": "carrier-demo-south"},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent dispatched wholesale shipment")

    r = _append(
        session,
        actor=agent,
        event_type="RefundIssued",
        occurred_at=_dt(day2, 12, 30),
        payload={"order_id": "ORD-DFT-2001", "amount": 1200, "receipt_hash": sha256(b"refund-ord-dft-2001").hexdigest()},
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Agent processed partial refund for day-2 order")

    public_day1 = publish_disclosure_run(
        session=session,
        policy_id="policy_public_v1",
        period_start=period1_start,
        period_end=period1_end,
        group_by=["channel"],
        actor=agent,
    )
    row = _find_disclosure_publish_event(session, public_day1.disclosure_id)
    if row is not None:
        remember(row, "Agent published public disclosure commitment for day 1")

    investor_day1 = publish_disclosure_run(
        session=session,
        policy_id="policy_investor_v1",
        period_start=period1_start,
        period_end=period1_end,
        group_by=["channel", "sku"],
        actor=agent,
    )
    row = _find_disclosure_publish_event(session, investor_day1.disclosure_id)
    if row is not None:
        remember(row, "Agent published investor disclosure with channel+sku granularity")

    public_day2_human = publish_disclosure_run(
        session=session,
        policy_id="policy_public_v1",
        period_start=period2_start,
        period_end=period2_end,
        group_by=["channel", "region"],
        actor=human,
    )
    row = _find_disclosure_publish_event(session, public_day2_human.disclosure_id)
    if row is not None:
        remember(row, "Human published day-2 public disclosure as governance checkpoint")

    auditor_day2 = publish_disclosure_run(
        session=session,
        policy_id="policy_auditor_v1",
        period_start=period2_start,
        period_end=period2_end,
        group_by=["channel", "sku"],
        actor=auditor,
    )
    row = _find_disclosure_publish_event(session, auditor_day2.disclosure_id)
    if row is not None:
        remember(row, "Auditor published selective-disclosure-ready report")

    disclosure_runs = {
        "public_day1": _minimal_disclosure(public_day1.payload),
        "investor_day1": _minimal_disclosure(investor_day1.payload),
        "public_day2_human": _minimal_disclosure(public_day2_human.payload),
        "auditor_day2": _minimal_disclosure(auditor_day2.payload),
    }

    actor_actions = {
        "agent": [x for x in action_log if x["actor"]["type"] == "agent"],
        "human": [x for x in action_log if x["actor"]["type"] == "human"],
        "auditor": [x for x in action_log if x["actor"]["type"] == "auditor"],
    }

    identity_ids: list[str] = []
    if actor_actions["agent"]:
        identity_ids.append(actor_actions["agent"][0]["event_id"])
    if actor_actions["human"]:
        identity_ids.append(actor_actions["human"][0]["event_id"])
    if actor_actions["auditor"]:
        identity_ids.append(actor_actions["auditor"][0]["event_id"])
    if len(actor_actions["agent"]) > 1:
        identity_ids.append(actor_actions["agent"][-1]["event_id"])
    if len(actor_actions["human"]) > 1:
        identity_ids.append(actor_actions["human"][-1]["event_id"])
    identity_ids = list(dict.fromkeys(identity_ids))

    result = {
        "scenario": "agent_primary_driver_with_human_safety_valve",
        "scenario_id": DEFAULT_SCENARIO_ID,
        "scenario_version": DEFAULT_SCENARIO_VERSION,
        "seeded_now": True,
        "period": {
            "start": period1_start.isoformat().replace("+00:00", "Z"),
            "end": period1_end.isoformat().replace("+00:00", "Z"),
        },
        "periods": [
            {
                "label": "day_1_operations",
                "start": period1_start.isoformat().replace("+00:00", "Z"),
                "end": period1_end.isoformat().replace("+00:00", "Z"),
            },
            {
                "label": "day_2_operations",
                "start": period2_start.isoformat().replace("+00:00", "Z"),
                "end": period2_end.isoformat().replace("+00:00", "Z"),
            },
        ],
        "purpose": "Demonstrate that agent can run daily operations while human remains safety valve for legal/high-risk actions, with cryptographic identity proofs and public verifiable disclosure.",
        "roles": {
            "agent": agent.model_dump(),
            "human": human.model_dump(),
            "auditor": auditor.model_dump(),
        },
        "storyline": [
            "Agent executes procurement, order handling, payment capture, shipping, and refunds as primary operator.",
            "Human signs the high-value procurement and high-risk bank transfer, proving safety-valve governance.",
            "Auditor role publishes selective-disclosure-ready report to prove external verification path.",
            "Public/Investor disclosures are committed via Merkle roots and anchored into immudb.",
        ],
        "agent_actions": actor_actions["agent"],
        "human_actions": actor_actions["human"],
        "auditor_actions": actor_actions["auditor"],
        "receipt_anchors": receipt_anchors,
        "public_disclosure": disclosure_runs["public_day1"],
        "investor_disclosure": disclosure_runs["investor_day1"],
        "extra_disclosures": [
            disclosure_runs["public_day2_human"],
            disclosure_runs["auditor_day2"],
        ],
        "how_to_verify": {
            "proof_api": f"/disclosure/{disclosure_runs['public_day1']['disclosure_id']}/proof?metric_key=revenue_cents",
            "anchor_api": f"/anchor/disclosure/{disclosure_runs['public_day1']['disclosure_id']}",
            "script": "python scripts/verify_disclosure.py --base-url http://localhost:8000 --disclosure-id <id> --metric-key revenue_cents",
        },
        "superset": {
            "url": "http://localhost:8088",
            "dashboard_url": "http://localhost:8088/superset/dashboard/transparent-company-default-story/",
            "username": "admin",
            "password": "admin",
            "recommended_datasets": [
                "public.disclosure_public_daily",
                "public.disclosure_investor_grouped",
                "public.disclosure_metrics",
            ],
            "recommended_charts": [
                "Daily revenue trend (public.disclosure_public_daily)",
                "Refund-rate trend (refunds_cents / revenue_cents)",
                "Investor revenue mix by channel+sku (public.disclosure_investor_grouped)",
            ],
        },
    }

    result["identity_proof"] = _identity_report(session, identity_ids)

    marker_payload = {
        "scenario_id": DEFAULT_SCENARIO_ID,
        "scenario_version": DEFAULT_SCENARIO_VERSION,
        "seeded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "key_event_ids": identity_ids,
        "result": result,
    }
    _append(
        session=session,
        actor=system,
        event_type="DemoScenarioInitialized",
        occurred_at=datetime.now(timezone.utc),
        payload=marker_payload,
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )

    return result


def get_default_scenario_story(session: Session) -> dict[str, Any]:
    marker = _marker_event(session)
    if marker is None:
        return seed_default_scenario(session)
    return _build_existing_response(session, marker)
