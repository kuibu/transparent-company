from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.utils import now_utc
from app.core.security import Actor
from app.disclosure.commitment import build_commitments
from app.disclosure.compute import compute_disclosure
from app.disclosure.policies import get_policy
from app.disclosure.statement import build_statement, sign_statement
from app.domain.accounting.reports import generate_pnl
from app.domain.projections import rebuild_all_read_models
from app.ledger.anchoring import AnchoringService
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.models import (
    DisclosureGroupedMetricModel,
    DisclosureMetricModel,
    DisclosureRunModel,
    LedgerEventModel,
)
from app.reconciliation.rules import run_minimum_reconciliation


@dataclass
class PublishResult:
    disclosure_id: str
    payload: dict


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _apply_redaction(grouped_metrics: list[dict], policy_id: str) -> list[dict]:
    policy = get_policy(policy_id)
    redacted: list[dict] = []
    for row in grouped_metrics:
        group = dict(row.get("group", {}))
        if not policy.redaction.allow_sku and "sku" in group:
            continue
        if policy.redaction.hide_supplier_id:
            group.pop("supplier_id", None)
        redacted.append({"metric_key": row["metric_key"], "group": group, "value": int(row["value"])})
    return redacted


def publish_disclosure_run(
    session: Session,
    policy_id: str,
    period_start: datetime,
    period_end: datetime,
    group_by: list[str] | None,
    actor: Actor,
) -> PublishResult:
    policy = get_policy(policy_id)
    period_start_utc = _as_utc(period_start)
    period_end_utc = _as_utc(period_end)
    all_events = list(session.scalars(select(LedgerEventModel).order_by(LedgerEventModel.seq_id.asc())).all())
    period_events = [event for event in all_events if period_start_utc <= _as_utc(event.occurred_at) < period_end_utc]

    shipment_costs = rebuild_all_read_models(session)
    period_shipment_costs = {
        event.event_id: shipment_costs.get(event.event_id, 0)
        for event in period_events
        if event.event_type == "ShipmentDispatched"
    }
    pnl_report = generate_pnl(period_events, shipment_costs=period_shipment_costs)

    computation = compute_disclosure(
        events=all_events,
        policy=policy,
        period_start=period_start_utc,
        period_end=period_end_utc,
        group_by=group_by,
        pnl_report=pnl_report,
    )

    grouped_metrics = _apply_redaction(computation.grouped_metrics, policy.policy_id)

    policy_hash = policy.policy_hash()
    period_obj = {
        "start": period_start_utc.isoformat().replace("+00:00", "Z"),
        "end": period_end_utc.isoformat().replace("+00:00", "Z"),
    }

    commitments = build_commitments(
        metrics=computation.metrics,
        grouped_metrics=grouped_metrics,
        policy_id=policy.policy_id,
        policy_hash=policy_hash,
        period=period_obj,
        proof_level=policy.proof_level,
        detail_event_map=computation.detail_event_map,
    )

    disclosure_id = str(uuid4())
    statement = build_statement(
        disclosure_id=disclosure_id,
        policy_id=policy.policy_id,
        policy_hash=policy_hash,
        period=period_obj,
        metrics=computation.metrics,
        grouped_metrics=grouped_metrics,
        root_summary=commitments.root_summary,
        root_details=commitments.root_details,
        proof_level=policy.proof_level,
        leaf_payloads=commitments.leaf_payloads,
    )
    reconciliation_results = run_minimum_reconciliation(
        events=period_events,
        disclosed_revenue_cents=int(computation.metrics.get("revenue_cents", 0)),
        pnl_report=pnl_report,
    )
    statement["reconciliation"] = [result.__dict__ for result in reconciliation_results]
    signer = load_role_key("agent")
    statement_signature, sig_hash = sign_statement(statement, signer)

    anchor_ref = AnchoringService(session).anchor_disclosure(
        disclosure_id=disclosure_id,
        policy_id=policy.policy_id,
        period=period_obj,
        root_summary=commitments.root_summary,
        root_details=commitments.root_details,
        statement_sig_hash=sig_hash,
    )

    run = DisclosureRunModel(
        disclosure_id=disclosure_id,
        policy_id=policy.policy_id,
        policy_hash=policy_hash,
        period_start=period_start_utc,
        period_end=period_end_utc,
        root_summary=commitments.root_summary,
        root_details=commitments.root_details,
        statement_json=statement,
        statement_signature=statement_signature,
        proof_index=commitments.proof_index,
        detail_index=commitments.detail_index,
        anchor_ref=anchor_ref,
        created_at=now_utc(),
    )
    session.add(run)
    # Session autoflush is disabled globally; ensure parent row exists before
    # inserting child rows constrained by FK(disclosure_id).
    session.flush()

    session.execute(delete(DisclosureMetricModel).where(DisclosureMetricModel.disclosure_id == disclosure_id))
    session.execute(delete(DisclosureGroupedMetricModel).where(DisclosureGroupedMetricModel.disclosure_id == disclosure_id))

    for key, value in computation.metrics.items():
        session.add(DisclosureMetricModel(disclosure_id=disclosure_id, metric_key=key, value=int(value)))
    for row in grouped_metrics:
        session.add(
            DisclosureGroupedMetricModel(
                disclosure_id=disclosure_id,
                metric_key=row["metric_key"],
                group_json=row["group"],
                value=int(row["value"]),
            )
        )

    publish_event = EventCreateRequest(
        event_type="DisclosurePublished",
        actor={"type": actor.type, "id": actor.id},
        policy_id=policy.policy_id,
        payload={
            "disclosure_id": disclosure_id,
            "policy_id": policy.policy_id,
            "period": period_obj,
            "metrics": computation.metrics,
            "merkle_root": commitments.root_summary,
            "anchor_ref": anchor_ref,
            "statement_sig_hash": sig_hash,
        },
    )
    LedgerStore(session).append(publish_event, signer=signer)

    payload = {
        "disclosure_id": disclosure_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy_hash,
        "period": period_obj,
        "metrics": computation.metrics,
        "grouped_metrics": grouped_metrics,
        "root_summary": commitments.root_summary,
        "root_details": commitments.root_details,
        "statement": statement,
        "statement_signature": statement_signature,
        "agent_public_key": signer.public_key_b64,
        "anchor_ref": anchor_ref,
    }

    return PublishResult(disclosure_id=disclosure_id, payload=payload)
