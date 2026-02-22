from __future__ import annotations

import csv
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.connectors import get_connector
from app.core.config import get_settings
from app.core.key_management import expected_signer_role
from app.core.security import Actor
from app.disclosure.publisher import publish_disclosure_run
from app.ledger.anchoring import AnchoringService
from app.ledger.events import EventCreateRequest
from app.ledger.receipts import build_receipt_store
from app.ledger.signing import load_role_key, verify_object
from app.ledger.store import LedgerStore
from app.persistence.models import LedgerEventModel


DEFAULT_SCENARIO_ID = "david_transparent_supermarket_q1_q2_story_v4"
DEFAULT_SCENARIO_VERSION = "3.2.0"

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUL_ROOT = REPO_ROOT / "transparent_supermarket"

SUPPLIERS = [
    {
        "supplier_id": "supplier-green-valley",
        "name": "Green Valley Farms",
        "category": "vegetable",
        "region": "jinan",
    },
    {
        "supplier_id": "supplier-ocean-live",
        "name": "OceanLive Seafood Chain",
        "category": "aquatic",
        "region": "qingdao",
    },
    {
        "supplier_id": "supplier-lanshan-tea",
        "name": "Lanshan Tea House",
        "category": "tea",
        "region": "wuyishan",
    },
    {
        "supplier_id": "supplier-qilu-fruit",
        "name": "Qilu Orchard Union",
        "category": "fruit",
        "region": "yantai",
    },
    {
        "supplier_id": "supplier-donghai-fresh",
        "name": "Donghai Fresh Fishery",
        "category": "aquatic",
        "region": "lianyungang",
    },
]

CUSTOMERS = [
    {"customer_id": "C001", "name": "Alice Lin", "segment": "family", "region": "jinan"},
    {"customer_id": "C002", "name": "Brian Wang", "segment": "family", "region": "qingdao"},
    {"customer_id": "C003", "name": "Cathy Zhou", "segment": "office", "region": "beijing"},
    {"customer_id": "C004", "name": "Daniel Chen", "segment": "family", "region": "shanghai"},
    {"customer_id": "C005", "name": "Evan Zhao", "segment": "restaurant", "region": "nanjing"},
    {"customer_id": "C006", "name": "Fiona Xu", "segment": "family", "region": "hangzhou"},
    {"customer_id": "C007", "name": "Gavin Sun", "segment": "family", "region": "jinan"},
    {"customer_id": "C008", "name": "Helen Han", "segment": "office", "region": "shenzhen"},
    {"customer_id": "C009", "name": "Iris Liu", "segment": "family", "region": "jinan"},
    {"customer_id": "C010", "name": "Jason Song", "segment": "restaurant", "region": "chengdu"},
]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_json(value: Any) -> str:
    return sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _export_root() -> Path:
    root = Path(get_settings().demo_exports_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


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
        if (
            payload.get("scenario_id") == DEFAULT_SCENARIO_ID
            and payload.get("scenario_version") == DEFAULT_SCENARIO_VERSION
        ):
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

    raw_amount = payload.get("amount_cents")
    if raw_amount is None:
        raw_amount = payload.get("amount")
    amount_cents = int(raw_amount) if raw_amount is not None else None

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
        "amount_cents": amount_cents,
        "supplier_id": payload.get("supplier_id") or payload.get("to"),
        "settlement_procurement_id": payload.get("settlement_procurement_id"),
        "purpose": payload.get("purpose"),
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
    method: str,
):
    object_key = f"payments/{order_id}-{paid_at.strftime('%Y%m%d%H%M')}.json"
    receipt = receipt_store.put_json(
        object_key=object_key,
        payload={
            "order_id": order_id,
            "amount": amount,
            "paid_at": paid_at.isoformat().replace("+00:00", "Z"),
            "provider": "transparent-supermarket-pay",
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
    return row, receipt_anchor


def _capture_compensation_receipt(
    session: Session,
    receipt_store,
    actor: Actor,
    conflict_id: str,
    order_id: str,
    amount: int,
    occurred_at: datetime,
):
    object_key = f"compensations/{conflict_id}-{occurred_at.strftime('%Y%m%d%H%M')}.json"
    receipt = receipt_store.put_json(
        object_key=object_key,
        payload={
            "conflict_id": conflict_id,
            "order_id": order_id,
            "amount": amount,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "handler": actor.id,
        },
    )
    return receipt


def _minimal_disclosure(disclosure_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "disclosure_id": disclosure_payload["disclosure_id"],
        "policy_id": disclosure_payload["policy_id"],
        "root_summary": disclosure_payload["root_summary"],
        "root_details": disclosure_payload["root_details"],
        "signer_role": disclosure_payload.get("signer_role"),
        "signer_public_key": disclosure_payload.get("signer_public_key"),
        "agent_public_key": disclosure_payload.get("agent_public_key"),
        "period": disclosure_payload.get("period"),
        "metrics": disclosure_payload.get("metrics", {}),
    }


def _collect_soul_manifest() -> list[dict[str, Any]]:
    if not SOUL_ROOT.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in sorted(SOUL_ROOT.rglob("*.md")):
        content = path.read_bytes()
        rel = path.relative_to(REPO_ROOT).as_posix()
        items.append(
            {
                "path": rel,
                "sha256": sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return items


def _build_superset_template() -> dict[str, Any]:
    return {
        "template_version": "2.0",
        "dashboard": {
            "title": "David Transparent Supermarket - Trust Dashboard",
            "slug": "david-transparent-supermarket-story",
            "description": "Daily + weekly + monthly disclosure layers with operational, quality, and cashflow KPIs.",
        },
        "database": {
            "name": "TransparentCompanyPG",
            "sqlalchemy_uri_env": "SUPERSET_ANALYTICS_DB_URI",
        },
        "datasets": [
            {"schema": "public", "table": "disclosure_public_daily_kpi_pretty"},
            {"schema": "public", "table": "disclosure_public_weekly_kpi_pretty"},
            {"schema": "public", "table": "disclosure_public_monthly_kpi_pretty"},
            {"schema": "public", "table": "disclosure_investor_revenue_dimension_pretty"},
            {"schema": "public", "table": "disclosure_investor_supplier_term_pretty"},
        ],
        "charts": [
            {
                "name": "Daily Revenue Trend (CNY)",
                "dataset": "public.disclosure_public_daily_kpi_pretty",
                "viz_type": "line",
                "metric": "revenue_yuan",
                "unit": "yuan",
            },
            {
                "name": "Daily Net Operating Cashflow (CNY)",
                "dataset": "public.disclosure_public_daily_kpi_pretty",
                "viz_type": "line",
                "metric": "operating_cash_net_inflow_yuan",
                "unit": "yuan",
            },
            {
                "name": "Daily Average Order Value (CNY)",
                "dataset": "public.disclosure_public_daily_kpi_pretty",
                "viz_type": "line",
                "metric": "avg_order_value_yuan",
                "unit": "yuan",
            },
            {
                "name": "Weekly Repeat Purchase Rate (%)",
                "dataset": "public.disclosure_public_weekly_kpi_pretty",
                "viz_type": "line",
                "metric": "repeat_purchase_rate_pct",
                "unit": "percent",
            },
            {
                "name": "Weekly QC Fail Rate (%)",
                "dataset": "public.disclosure_public_weekly_kpi_pretty",
                "viz_type": "dist_bar",
                "metric": "qc_fail_rate_pct",
                "unit": "percent",
            },
            {
                "name": "Weekly Complaint Resolution Hours",
                "dataset": "public.disclosure_public_weekly_kpi_pretty",
                "viz_type": "line",
                "metric": "complaint_resolution_hours_avg",
                "unit": "hours",
            },
            {
                "name": "Monthly Inventory Turnover Days",
                "dataset": "public.disclosure_public_monthly_kpi_pretty",
                "viz_type": "dist_bar",
                "metric": "inventory_turnover_days",
                "unit": "days",
            },
            {
                "name": "Monthly Slow-moving SKU Ratio (%)",
                "dataset": "public.disclosure_public_monthly_kpi_pretty",
                "viz_type": "line",
                "metric": "slow_moving_sku_ratio_pct",
                "unit": "percent",
            },
            {
                "name": "Promotion Phase Revenue Mix (CNY)",
                "dataset": "public.disclosure_investor_revenue_dimension_pretty",
                "viz_type": "rose",
                "metric": "revenue_yuan",
                "groupby": ["promotion_phase"],
                "unit": "yuan",
            },
            {
                "name": "Supplier Payment Term Structure (CNY)",
                "dataset": "public.disclosure_investor_supplier_term_pretty",
                "viz_type": "rose",
                "metric": "settlement_yuan",
                "groupby": ["payment_term_bucket"],
                "unit": "yuan",
            },
        ],
    }

def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _scenario_events_for_export(session: Session, disclosure_ids: set[str]) -> list[LedgerEventModel]:
    rows = list(session.scalars(select(LedgerEventModel).order_by(LedgerEventModel.seq_id.asc())).all())
    out: list[LedgerEventModel] = []
    for row in rows:
        tool_trace = row.tool_trace or {}
        payload = row.payload or {}
        if tool_trace.get("scenario_id") == DEFAULT_SCENARIO_ID:
            out.append(row)
            continue
        if row.event_type == "DisclosurePublished" and payload.get("disclosure_id") in disclosure_ids:
            out.append(row)
            continue
        if row.event_type == "DemoScenarioInitialized" and payload.get("scenario_id") == DEFAULT_SCENARIO_ID:
            out.append(row)
    return out


def _export_story_artifacts(
    session: Session,
    disclosure_ids: set[str],
    suppliers: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    bank_transactions: list[dict[str, Any]],
    soul_manifest: list[dict[str, Any]],
) -> dict[str, str]:
    export_root = _export_root()

    events = _scenario_events_for_export(session, disclosure_ids)

    event_json_rows = []
    event_csv_rows = []
    for row in events:
        event_json_rows.append(
            {
                "seq_id": row.seq_id,
                "event_id": row.event_id,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "actor": {"type": row.actor_type, "id": row.actor_id},
                "policy_id": row.policy_id,
                "payload": row.payload,
                "tool_trace": row.tool_trace,
                "prev_hash": row.prev_hash,
                "event_hash": row.event_hash,
                "signature": row.signature,
            }
        )
        event_csv_rows.append(
            {
                "seq_id": row.seq_id,
                "event_id": row.event_id,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at.isoformat().replace("+00:00", "Z"),
                "actor_type": row.actor_type,
                "actor_id": row.actor_id,
                "policy_id": row.policy_id,
                "payload_json": _json_dump(row.payload),
                "tool_trace_json": _json_dump(row.tool_trace),
                "prev_hash": row.prev_hash,
                "event_hash": row.event_hash,
                "signature": row.signature,
            }
        )

    events_json_path = export_root / "david_transparent_supermarket_q1_q2_events.json"
    events_csv_path = export_root / "david_transparent_supermarket_q1_q2_events.csv"
    bank_csv_path = export_root / "david_transparent_supermarket_q1_q2_bank_transactions.csv"
    bank_json_path = export_root / "david_transparent_supermarket_q1_q2_bank_transactions.json"
    suppliers_csv_path = export_root / "david_transparent_supermarket_suppliers.csv"
    customers_csv_path = export_root / "david_transparent_supermarket_customers.csv"
    soul_json_path = export_root / "david_transparent_supermarket_soul_manifest.json"
    superset_template_path = export_root / "david_transparent_supermarket_superset_dashboard_template.json"

    events_json_path.write_text(json.dumps(event_json_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(
        events_csv_path,
        event_csv_rows,
        [
            "seq_id",
            "event_id",
            "event_type",
            "occurred_at",
            "actor_type",
            "actor_id",
            "policy_id",
            "payload_json",
            "tool_trace_json",
            "prev_hash",
            "event_hash",
            "signature",
        ],
    )

    bank_json_path.write_text(json.dumps(bank_transactions, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(
        bank_csv_path,
        bank_transactions,
        [
            "tx_id",
            "occurred_at",
            "direction",
            "counterparty",
            "amount_cents",
            "currency",
            "subject",
            "actor_type",
            "actor_id",
            "reference",
        ],
    )

    _write_csv(
        suppliers_csv_path,
        suppliers,
        ["supplier_id", "name", "category", "region"],
    )
    _write_csv(
        customers_csv_path,
        customers,
        ["customer_id", "name", "segment", "region"],
    )

    soul_json_path.write_text(json.dumps(soul_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    superset_template_path.write_text(
        json.dumps(_build_superset_template(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "events_json": _display_path(events_json_path),
        "events_csv": _display_path(events_csv_path),
        "bank_transactions_csv": _display_path(bank_csv_path),
        "bank_transactions_json": _display_path(bank_json_path),
        "suppliers_csv": _display_path(suppliers_csv_path),
        "customers_csv": _display_path(customers_csv_path),
        "soul_manifest_json": _display_path(soul_json_path),
        "superset_template_json": _display_path(superset_template_path),
    }


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


def _refresh_superset_recommendations(result: dict[str, Any]) -> None:
    template = _build_superset_template()
    datasets = [
        f"{item['schema']}.{item['table']}"
        for item in template.get("datasets", [])
        if item.get("schema") and item.get("table")
    ]
    charts = [item.get("name") for item in template.get("charts", []) if item.get("name")]

    dashboard_slug = template.get("dashboard", {}).get("slug", "david-transparent-supermarket-story")

    superset = dict(result.get("superset", {}))
    superset.setdefault("url", "http://localhost:8088")
    superset.setdefault("dashboard_url", f"http://localhost:8088/superset/dashboard/{dashboard_slug}/")
    superset.setdefault("username", "admin")
    superset.setdefault("password", "admin")
    superset["recommended_datasets"] = datasets
    superset["recommended_charts"] = charts
    result["superset"] = superset


def _apply_public_detail_level(result: dict[str, Any], detail_level: str) -> dict[str, Any]:
    mode = (detail_level or "summary").strip().lower()
    if mode not in {"summary", "full"}:
        raise ValueError("detail_level must be summary or full")

    out = deepcopy(result)
    out["public_detail_level"] = mode
    out["available_public_detail_levels"] = ["summary", "full"]

    if mode == "full":
        return out

    bank_transactions = out.pop("bank_transactions", [])
    customers = out.pop("customers", [])
    partners = out.pop("partners", [])

    bank_inflow = sum(int(item.get("amount_cents", 0)) for item in bank_transactions if item.get("direction") == "in")
    bank_outflow = sum(int(item.get("amount_cents", 0)) for item in bank_transactions if item.get("direction") == "out")

    out["customer_summary"] = {
        "customer_count": len(customers),
        "note": "summary mode hides identifiable customer names",
    }
    out["bank_transaction_summary"] = {
        "tx_count": len(bank_transactions),
        "inflow_cents": bank_inflow,
        "outflow_cents": bank_outflow,
        "net_cents": bank_inflow - bank_outflow,
        "note": "summary mode hides counterparty-level bank details",
    }
    categories = sorted({str(item.get("category")) for item in partners if item.get("category")})
    regions = sorted({str(item.get("region")) for item in partners if item.get("region")})
    out["supplier_summary"] = {
        "supplier_count": len(partners),
        "category_count": len(categories),
        "region_count": len(regions),
        "categories": categories,
        "regions": regions,
        "note": "summary mode hides identifiable supplier names",
    }

    data_exports = dict(out.get("data_exports", {}))
    if data_exports:
        out["data_exports"] = {
            "soul_manifest_json": data_exports.get("soul_manifest_json"),
            "superset_template_json": data_exports.get("superset_template_json"),
            "note": "full event/bank/customer exports are available in detail_level=full",
        }

    return out


def _build_existing_response(session: Session, marker: LedgerEventModel, detail_level: str = "summary") -> dict[str, Any]:
    payload = deepcopy(marker.payload or {})
    result = deepcopy(payload.get("result", {}))
    key_event_ids = payload.get("key_event_ids", [])
    result["seeded_now"] = False
    result["identity_proof"] = _identity_report(session, key_event_ids)

    soul_manifest = _collect_soul_manifest()
    result["soul_manifest"] = soul_manifest

    company = dict(result.get("company", {}))
    if company:
        company["soul_manifest_hash"] = _sha256_json(soul_manifest)
        result["company"] = company

    disclosure_ids: set[str] = set()
    for key in ("public_disclosure", "investor_disclosure"):
        details = result.get(key) or {}
        disclosure_id = details.get("disclosure_id")
        if disclosure_id:
            disclosure_ids.add(disclosure_id)

    for bucket in ("extra_disclosures", "public_daily_disclosures", "public_weekly_disclosures", "public_monthly_disclosures", "investor_weekly_disclosures", "investor_monthly_disclosures"):
        for details in result.get(bucket, []) or []:
            disclosure_id = (details or {}).get("disclosure_id")
            if disclosure_id:
                disclosure_ids.add(disclosure_id)

    if disclosure_ids:
        result["data_exports"] = _export_story_artifacts(
            session=session,
            disclosure_ids=disclosure_ids,
            suppliers=SUPPLIERS,
            customers=CUSTOMERS,
            bank_transactions=result.get("bank_transactions", []),
            soul_manifest=soul_manifest,
        )

    _refresh_superset_recommendations(result)

    return _apply_public_detail_level(result, detail_level)


def seed_default_scenario(session: Session, detail_level: str = "full") -> dict[str, Any]:
    marker = _marker_event(session)
    if marker is not None:
        return _build_existing_response(session, marker, detail_level=detail_level)

    ceo = Actor(type="agent", id="agent-david-ceo")
    sales_agent = Actor(type="agent", id="agent-sales-001")
    qc_agent = Actor(type="agent", id="agent-qc-001")
    refund_agent = Actor(type="agent", id="agent-refund-001")
    complaint_agent = Actor(type="agent", id="agent-complaint-001")
    logistics_agent = Actor(type="agent", id="agent-logistics-001")

    human = Actor(type="human", id="human-xu-dawei")
    auditor = Actor(type="auditor", id="auditor-proof-001")
    system = Actor(type="system", id="system-bootstrap-001")

    receipt_store = build_receipt_store()
    anchor_service = AnchoringService(session)

    key_rows: list[LedgerEventModel] = []
    action_log: list[dict[str, Any]] = []
    receipt_anchors: list[dict[str, Any]] = []
    bank_transactions: list[dict[str, Any]] = []

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

    def add_bank_tx(
        tx_id: str,
        occurred_at: datetime,
        direction: str,
        counterparty: str,
        amount_cents: int,
        subject: str,
        actor: Actor,
        reference: str,
    ) -> None:
        bank_transactions.append(
            {
                "tx_id": tx_id,
                "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                "direction": direction,
                "counterparty": counterparty,
                "amount_cents": int(amount_cents),
                "currency": "CNY",
                "subject": subject,
                "actor_type": actor.type,
                "actor_id": actor.id,
                "reference": reference,
            }
        )

    procurements = [
        {
            "procurement_id": "PO-20250105-TOMATO",
            "actor": ceo,
            "supplier_id": "supplier-green-valley",
            "items": [{"sku": "tomato", "qty": 500, "unit_cost": 320}],
            "ordered_at": _ts("2025-01-05T08:30:00Z"),
            "expected_date": "2025-01-07",
            "received_at": _ts("2025-01-07T09:10:00Z"),
            "batch_id": "BATCH-TOMATO-20250107",
            "expiry_days": 7,
            "quality_note": "无公害抽检通过",
        },
        {
            "procurement_id": "PO-20250110-FISH",
            "actor": ceo,
            "supplier_id": "supplier-ocean-live",
            "items": [{"sku": "fish", "qty": 200, "unit_cost": 2100}],
            "ordered_at": _ts("2025-01-10T06:40:00Z"),
            "expected_date": "2025-01-12",
            "received_at": _ts("2025-01-12T07:30:00Z"),
            "batch_id": "BATCH-FISH-20250112",
            "expiry_days": 2,
            "quality_note": "活鱼检测合格",
        },
        {
            "procurement_id": "PO-20250112-TEA",
            "actor": human,
            "supplier_id": "supplier-lanshan-tea",
            "items": [{"sku": "tea", "qty": 100, "unit_cost": 8000}],
            "ordered_at": _ts("2025-01-12T10:00:00Z"),
            "expected_date": "2025-01-13",
            "received_at": _ts("2025-01-13T10:20:00Z"),
            "batch_id": "BATCH-TEA-20250113",
            "expiry_days": 180,
            "quality_note": "茶叶水分检测通过",
        },
        {
            "procurement_id": "PO-20250202-TOMATO",
            "actor": ceo,
            "supplier_id": "supplier-green-valley",
            "items": [{"sku": "tomato", "qty": 450, "unit_cost": 330}],
            "ordered_at": _ts("2025-02-02T07:45:00Z"),
            "expected_date": "2025-02-03",
            "received_at": _ts("2025-02-03T08:20:00Z"),
            "batch_id": "BATCH-TOMATO-20250203",
            "expiry_days": 6,
            "quality_note": "抽检通过",
        },
        {
            "procurement_id": "PO-20250203-FISH",
            "actor": ceo,
            "supplier_id": "supplier-ocean-live",
            "items": [{"sku": "fish", "qty": 180, "unit_cost": 2200}],
            "ordered_at": _ts("2025-02-03T06:50:00Z"),
            "expected_date": "2025-02-04",
            "received_at": _ts("2025-02-04T07:15:00Z"),
            "batch_id": "BATCH-FISH-20250204",
            "expiry_days": 2,
            "quality_note": "活鱼检测合格",
        },
        {
            "procurement_id": "PO-20250205-TEA",
            "actor": ceo,
            "supplier_id": "supplier-lanshan-tea",
            "items": [{"sku": "tea", "qty": 60, "unit_cost": 8200}],
            "ordered_at": _ts("2025-02-05T09:00:00Z"),
            "expected_date": "2025-02-06",
            "received_at": _ts("2025-02-06T09:30:00Z"),
            "batch_id": "BATCH-TEA-20250206",
            "expiry_days": 170,
            "quality_note": "茶叶复检通过",
        },
        {
            "procurement_id": "PO-20250303-TOMATO",
            "actor": ceo,
            "supplier_id": "supplier-green-valley",
            "items": [{"sku": "tomato", "qty": 400, "unit_cost": 340}],
            "ordered_at": _ts("2025-03-03T07:35:00Z"),
            "expected_date": "2025-03-04",
            "received_at": _ts("2025-03-04T08:10:00Z"),
            "batch_id": "BATCH-TOMATO-20250304",
            "expiry_days": 7,
            "quality_note": "抽检通过",
        },
        {
            "procurement_id": "PO-20250305-TEA",
            "actor": human,
            "supplier_id": "supplier-lanshan-tea",
            "items": [{"sku": "tea", "qty": 90, "unit_cost": 8000}],
            "ordered_at": _ts("2025-03-05T10:00:00Z"),
            "expected_date": "2025-03-06",
            "received_at": _ts("2025-03-06T10:20:00Z"),
            "batch_id": "BATCH-TEA-20250306",
            "expiry_days": 160,
            "quality_note": "茶叶水分检测通过",
        },
        {
            "procurement_id": "PO-20250410-APPLE",
            "actor": ceo,
            "supplier_id": "supplier-qilu-fruit",
            "items": [{"sku": "apple", "qty": 400, "unit_cost": 320}],
            "ordered_at": _ts("2025-04-10T08:10:00Z"),
            "expected_date": "2025-04-12",
            "received_at": _ts("2025-04-12T08:50:00Z"),
            "batch_id": "BATCH-APPLE-20250412",
            "expiry_days": 20,
            "quality_note": "苹果糖度抽检通过",
        },
        {
            "procurement_id": "PO-20250415-TOMATO",
            "actor": ceo,
            "supplier_id": "supplier-green-valley",
            "items": [{"sku": "tomato", "qty": 380, "unit_cost": 350}],
            "ordered_at": _ts("2025-04-15T07:20:00Z"),
            "expected_date": "2025-04-16",
            "received_at": _ts("2025-04-16T07:55:00Z"),
            "batch_id": "BATCH-TOMATO-20250416",
            "expiry_days": 7,
            "quality_note": "抽检通过",
        },
        {
            "procurement_id": "PO-20250520-FISH",
            "actor": human,
            "supplier_id": "supplier-donghai-fresh",
            "items": [{"sku": "fish", "qty": 250, "unit_cost": 2400}],
            "ordered_at": _ts("2025-05-20T09:30:00Z"),
            "expected_date": "2025-05-21",
            "received_at": _ts("2025-05-21T10:00:00Z"),
            "batch_id": "BATCH-FISH-20250521",
            "expiry_days": 2,
            "quality_note": "新供应商首批检测合格",
        },
        {
            "procurement_id": "PO-20250522-APPLE",
            "actor": ceo,
            "supplier_id": "supplier-qilu-fruit",
            "items": [{"sku": "apple", "qty": 420, "unit_cost": 330}],
            "ordered_at": _ts("2025-05-22T08:00:00Z"),
            "expected_date": "2025-05-23",
            "received_at": _ts("2025-05-23T08:35:00Z"),
            "batch_id": "BATCH-APPLE-20250523",
            "expiry_days": 20,
            "quality_note": "苹果抽检通过",
        },
        {
            "procurement_id": "PO-20250603-TOMATO",
            "actor": ceo,
            "supplier_id": "supplier-green-valley",
            "items": [{"sku": "tomato", "qty": 420, "unit_cost": 360}],
            "ordered_at": _ts("2025-06-03T07:40:00Z"),
            "expected_date": "2025-06-04",
            "received_at": _ts("2025-06-04T08:15:00Z"),
            "batch_id": "BATCH-TOMATO-20250604",
            "expiry_days": 7,
            "quality_note": "抽检通过",
        },
        {
            "procurement_id": "PO-20250610-APPLE",
            "actor": ceo,
            "supplier_id": "supplier-qilu-fruit",
            "items": [{"sku": "apple", "qty": 450, "unit_cost": 335}],
            "ordered_at": _ts("2025-06-10T08:10:00Z"),
            "expected_date": "2025-06-11",
            "received_at": _ts("2025-06-11T08:45:00Z"),
            "batch_id": "BATCH-APPLE-20250611",
            "expiry_days": 18,
            "quality_note": "苹果抽检通过",
        },
    ]

    for idx, po in enumerate(procurements, start=1):
        r_tool = _log_tool_invocation(
            session,
            actor=po["actor"],
            connector_name="supplier",
            action="place_order",
            payload={"supplier_id": po["supplier_id"], "items": po["items"]},
            run_id=f"run-tsm-q1q2-po-{idx:03d}",
            task_id=f"task-place-order-{po['procurement_id']}",
            occurred_at=po["ordered_at"] - timedelta(minutes=20),
        )
        remember(r_tool, "供应商系统下单调用")

        r = _append(
            session,
            actor=po["actor"],
            event_type="ProcurementOrdered",
            occurred_at=po["ordered_at"],
            payload={
                "procurement_id": po["procurement_id"],
                "supplier_id": po["supplier_id"],
                "items": po["items"],
                "expected_date": po["expected_date"],
            },
            tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
        )
        remember(r, f"采购下单：{po['procurement_id']}")

        recv_items = []
        for item in po["items"]:
            recv_items.append(
                {
                    "sku": item["sku"],
                    "qty": item["qty"],
                    "expiry_date": (po["received_at"].date() + timedelta(days=po["expiry_days"])).isoformat(),
                    "unit_cost": item["unit_cost"],
                }
            )

        r = _append(
            session,
            actor=qc_agent,
            event_type="GoodsReceived",
            occurred_at=po["received_at"],
            payload={
                "procurement_id": po["procurement_id"],
                "batch_id": po["batch_id"],
                "items": recv_items,
                "qc_passed": True,
            },
            tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "quality_note": po["quality_note"]},
        )
        remember(r, f"收货质检：{po['quality_note']}")

    r = _append(
        session,
        actor=qc_agent,
        event_type="GoodsReceived",
        occurred_at=_ts("2025-06-15T07:40:00Z"),
        payload={
            "procurement_id": "PO-20250615-FISH-QC",
            "batch_id": "BATCH-FISH-20250615-FAIL",
            "items": [{"sku": "fish", "qty": 22, "expiry_date": "2025-06-17", "unit_cost": 2450}],
            "qc_passed": False,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "quality_note": "fish sample rejected due to freshness"},
    )
    remember(r, "QC Agent rejected a fish batch")

    # Human signs supplier contract switch in Q2.
    contract_payload = {
        "contract_id": "CONTRACT-20250520-DONGHAI",
        "supplier_id": "supplier-donghai-fresh",
        "signed_by": human.id,
        "effective_date": "2025-05-20",
        "terms_hash": _sha256_json({"supplier": "supplier-donghai-fresh", "version": "v1"}),
    }
    r = _append(
        session,
        actor=human,
        event_type="SupplierContractSigned",
        occurred_at=_ts("2025-05-20T09:00:00Z"),
        payload=contract_payload,
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "offline_contract": True},
    )
    remember(r, "Human 完成活鱼供应商线下签约")

    r = _append(
        session,
        actor=ceo,
        event_type="PolicyUpdated",
        occurred_at=_ts("2025-05-21T08:20:00Z"),
        payload={
            "policy_domain": "procurement",
            "previous_version": "procurement_policy_v1",
            "new_version": "procurement_policy_v2",
            "policy_hash": _sha256_json({"domain": "procurement", "version": "v2", "fish_supplier": "supplier-donghai-fresh"}),
            "reason": "Switched fish supplier after human-signed contract",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "David 更新采购策略版本")

    order_specs = [
        {
            "order_id": "ORD-20250115-01",
            "customer_ref": "C001:Alice Lin",
            "items": [{"sku": "tomato", "qty": 80, "unit_price": 620}],
            "channel": "store",
            "region": "jinan",
            "placed_at": _ts("2025-01-15T10:00:00Z"),
            "paid_at": _ts("2025-01-15T10:05:00Z"),
            "shipped_at": _ts("2025-01-15T10:35:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250118-01",
            "customer_ref": "C002:Brian Wang",
            "items": [{"sku": "fish", "qty": 40, "unit_price": 3600}],
            "channel": "store",
            "region": "qingdao",
            "placed_at": _ts("2025-01-18T11:00:00Z"),
            "paid_at": _ts("2025-01-18T11:04:00Z"),
            "shipped_at": _ts("2025-01-18T11:20:00Z"),
            "method": "card",
        },
        {
            "order_id": "ORD-20250125-01",
            "customer_ref": "C003:Cathy Zhou",
            "items": [{"sku": "tea", "qty": 25, "unit_price": 9800}],
            "channel": "online",
            "region": "beijing",
            "placed_at": _ts("2025-01-25T12:10:00Z"),
            "paid_at": _ts("2025-01-25T12:12:00Z"),
            "shipped_at": _ts("2025-01-25T12:40:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250208-01",
            "customer_ref": "C004:Daniel Chen",
            "items": [{"sku": "tomato", "qty": 120, "unit_price": 630}],
            "channel": "online",
            "region": "shanghai",
            "placed_at": _ts("2025-02-08T09:15:00Z"),
            "paid_at": _ts("2025-02-08T09:16:00Z"),
            "shipped_at": _ts("2025-02-08T09:45:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250211-01",
            "customer_ref": "C005:Evan Zhao",
            "items": [{"sku": "fish", "qty": 35, "unit_price": 3650}],
            "channel": "wholesale",
            "region": "nanjing",
            "placed_at": _ts("2025-02-11T08:40:00Z"),
            "paid_at": _ts("2025-02-11T08:42:00Z"),
            "shipped_at": _ts("2025-02-11T09:05:00Z"),
            "method": "bank_transfer",
        },
        {
            "order_id": "ORD-20250220-01",
            "customer_ref": "C006:Fiona Xu",
            "items": [{"sku": "tea", "qty": 20, "unit_price": 9800}],
            "channel": "store",
            "region": "hangzhou",
            "placed_at": _ts("2025-02-20T13:10:00Z"),
            "paid_at": _ts("2025-02-20T13:11:00Z"),
            "shipped_at": _ts("2025-02-20T13:50:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250307-01",
            "customer_ref": "C007:Gavin Sun",
            "items": [{"sku": "tomato", "qty": 110, "unit_price": 640}],
            "channel": "store",
            "region": "jinan",
            "placed_at": _ts("2025-03-07T10:20:00Z"),
            "paid_at": _ts("2025-03-07T10:23:00Z"),
            "shipped_at": _ts("2025-03-07T10:55:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250314-01",
            "customer_ref": "C008:Helen Han",
            "items": [{"sku": "fish", "qty": 30, "unit_price": 3700}],
            "channel": "online",
            "region": "shenzhen",
            "placed_at": _ts("2025-03-14T11:00:00Z"),
            "paid_at": _ts("2025-03-14T11:02:00Z"),
            "shipped_at": _ts("2025-03-14T11:25:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250322-01",
            "customer_ref": "C009:Iris Liu",
            "items": [{"sku": "tea", "qty": 30, "unit_price": 9900}],
            "channel": "store",
            "region": "jinan",
            "placed_at": _ts("2025-03-22T14:00:00Z"),
            "paid_at": _ts("2025-03-22T14:03:00Z"),
            "shipped_at": _ts("2025-03-22T14:35:00Z"),
            "method": "card",
        },
        {
            "order_id": "ORD-20250412-01",
            "customer_ref": "C010:Jason Song",
            "items": [{"sku": "apple", "qty": 140, "unit_price": 410}],
            "channel": "store",
            "region": "chengdu",
            "placed_at": _ts("2025-04-12T09:30:00Z"),
            "paid_at": _ts("2025-04-12T09:31:00Z"),
            "shipped_at": _ts("2025-04-12T10:05:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250418-01",
            "customer_ref": "C003:Cathy Zhou",
            "items": [
                {"sku": "tomato", "qty": 100, "unit_price": 650},
                {"sku": "apple", "qty": 60, "unit_price": 420},
            ],
            "channel": "online",
            "region": "beijing",
            "placed_at": _ts("2025-04-18T13:20:00Z"),
            "paid_at": _ts("2025-04-18T13:21:00Z"),
            "shipped_at": _ts("2025-04-18T13:55:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250509-01",
            "customer_ref": "C005:Evan Zhao",
            "items": [{"sku": "fish", "qty": 55, "unit_price": 3800}],
            "channel": "wholesale",
            "region": "nanjing",
            "placed_at": _ts("2025-05-09T08:40:00Z"),
            "paid_at": _ts("2025-05-09T08:42:00Z"),
            "shipped_at": _ts("2025-05-09T09:00:00Z"),
            "method": "bank_transfer",
        },
        {
            "order_id": "ORD-20250521-01",
            "customer_ref": "C006:Fiona Xu",
            "items": [{"sku": "tea", "qty": 28, "unit_price": 9900}],
            "channel": "store",
            "region": "hangzhou",
            "placed_at": _ts("2025-05-21T15:00:00Z"),
            "paid_at": _ts("2025-05-21T15:02:00Z"),
            "shipped_at": _ts("2025-05-21T15:30:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250526-01",
            "customer_ref": "C010:Jason Song",
            "items": [{"sku": "apple", "qty": 130, "unit_price": 415}],
            "channel": "online",
            "region": "chengdu",
            "placed_at": _ts("2025-05-26T10:10:00Z"),
            "paid_at": _ts("2025-05-26T10:11:00Z"),
            "shipped_at": _ts("2025-05-26T10:40:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250605-01",
            "customer_ref": "C002:Brian Wang",
            "items": [{"sku": "fish", "qty": 60, "unit_price": 3900}],
            "channel": "store",
            "region": "qingdao",
            "placed_at": _ts("2025-06-05T11:30:00Z"),
            "paid_at": _ts("2025-06-05T11:31:00Z"),
            "shipped_at": _ts("2025-06-05T11:50:00Z"),
            "method": "card",
        },
        {
            "order_id": "ORD-20250612-01",
            "customer_ref": "C001:Alice Lin",
            "items": [{"sku": "tomato", "qty": 150, "unit_price": 660}],
            "channel": "online",
            "region": "jinan",
            "placed_at": _ts("2025-06-12T09:00:00Z"),
            "paid_at": _ts("2025-06-12T09:02:00Z"),
            "shipped_at": _ts("2025-06-12T09:30:00Z"),
            "method": "alipay",
        },
        {
            "order_id": "ORD-20250622-01",
            "customer_ref": "C004:Daniel Chen",
            "items": [{"sku": "apple", "qty": 170, "unit_price": 425}],
            "channel": "store",
            "region": "shanghai",
            "placed_at": _ts("2025-06-22T14:10:00Z"),
            "paid_at": _ts("2025-06-22T14:12:00Z"),
            "shipped_at": _ts("2025-06-22T14:45:00Z"),
            "method": "wechat",
        },
        {
            "order_id": "ORD-20250624-01",
            "customer_ref": "C008:Helen Han",
            "items": [{"sku": "tea", "qty": 35, "unit_price": 9950}],
            "channel": "online",
            "region": "shenzhen",
            "placed_at": _ts("2025-06-24T12:00:00Z"),
            "paid_at": _ts("2025-06-24T12:01:00Z"),
            "shipped_at": _ts("2025-06-24T12:30:00Z"),
            "method": "alipay",
        },
    ]

    def infer_time_slot(ts: datetime) -> str:
        hour = ts.hour
        if hour < 11:
            return "morning"
        if hour < 15:
            return "midday"
        if hour < 19:
            return "afternoon"
        return "evening"

    def infer_store_id(channel: str, region: str) -> str:
        if channel == "store":
            return f"store_{region}_01"
        if channel == "wholesale":
            return "dc_wholesale_hub"
        return "online_fulfillment_center"

    campaign_start = _ts("2025-06-10T00:00:00Z")
    campaign_end = _ts("2025-06-20T23:59:59Z")

    for idx in range(30):
        day_start = _ts(f"2025-06-{idx + 1:02d}T00:00:00Z")
        placed_at = day_start + timedelta(hours=9 + (idx % 10))
        paid_at = placed_at + timedelta(minutes=2)
        shipped_at = placed_at + timedelta(minutes=40)

        customer = CUSTOMERS[idx % len(CUSTOMERS)]
        sku_cycle = ["tomato", "apple", "tomato", "apple", "fish", "tea"]
        sku = sku_cycle[idx % len(sku_cycle)]
        if sku in {"fish", "tea"}:
            qty = 3 + (idx % 3)
        else:
            qty = 20 + (idx % 7) * 3
        base_price = {"tomato": 660, "apple": 430, "tea": 9900, "fish": 3900}[sku]

        if campaign_start <= placed_at <= campaign_end:
            promotion_phase = "promo"
            unit_price = int(base_price * 0.93)
        elif placed_at < campaign_start:
            promotion_phase = "pre_promo"
            unit_price = base_price
        else:
            promotion_phase = "post_promo"
            unit_price = int(base_price * 0.97)

        channel = "store" if idx % 3 != 0 else "online"
        region = str(customer["region"])
        order_specs.append(
            {
                "order_id": f"ORD-202506X-{idx + 1:02d}",
                "customer_ref": f"{customer['customer_id']}:{customer['name']}",
                "items": [{"sku": sku, "qty": qty, "unit_price": unit_price}],
                "channel": channel,
                "region": region,
                "placed_at": placed_at,
                "paid_at": paid_at,
                "shipped_at": shipped_at,
                "method": "wechat" if channel == "store" else "alipay",
                "promotion_id": "summer_fresh_campaign_2025",
                "promotion_phase": promotion_phase,
            }
        )

    for order in order_specs:
        order.setdefault("store_id", infer_store_id(order["channel"], order["region"]))
        order.setdefault("time_slot", infer_time_slot(order["placed_at"]))
        if campaign_start <= order["placed_at"] <= campaign_end:
            phase = "promo"
        elif order["placed_at"] < campaign_start:
            phase = "pre_promo"
        else:
            phase = "post_promo"
        order.setdefault("promotion_phase", phase)
        order.setdefault("promotion_id", "summer_fresh_campaign_2025" if phase != "pre_promo" else "baseline_2025")

    for order in order_specs:
        amount = sum(int(item["qty"]) * int(item["unit_price"]) for item in order["items"])

        r = _append(
            session,
            actor=sales_agent,
            event_type="OrderPlaced",
            occurred_at=order["placed_at"],
            payload={
                "order_id": order["order_id"],
                "customer_ref": order["customer_ref"],
                "items": order["items"],
                "channel": order["channel"],
                "region": order["region"],
                "store_id": order.get("store_id"),
                "time_slot": order.get("time_slot"),
                "promotion_id": order.get("promotion_id"),
                "promotion_phase": order.get("promotion_phase"),
            },
            tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
        )
        remember(r, f"Sales Agent 接单：{order['order_id']}")

        r_pay, anchor = _capture_payment(
            session=session,
            anchor_service=anchor_service,
            receipt_store=receipt_store,
            actor=sales_agent,
            order_id=order["order_id"],
            amount=amount,
            paid_at=order["paid_at"],
            method=order["method"],
        )
        remember(r_pay, f"Sales Agent 收款：{order['order_id']}")
        receipt_anchors.append(anchor)
        add_bank_tx(
            tx_id=f"IN-{order['order_id']}",
            occurred_at=order["paid_at"],
            direction="in",
            counterparty=order["customer_ref"],
            amount_cents=amount,
            subject="order_payment",
            actor=sales_agent,
            reference=order["order_id"],
        )

        r = _append(
            session,
            actor=logistics_agent,
            event_type="ShipmentDispatched",
            occurred_at=order["shipped_at"],
            payload={
                "order_id": order["order_id"],
                "items": [{"sku": item["sku"], "qty": item["qty"]} for item in order["items"]],
                "carrier_ref": "logistics-transparent-route",
            },
            tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
        )
        remember(r, f"Logistics Agent 发货：{order['order_id']}")

    # Q1 loss event: tomato expired and removed from shelf.
    r = _append(
        session,
        actor=qc_agent,
        event_type="InventoryAdjusted",
        occurred_at=_ts("2025-02-14T18:20:00Z"),
        payload={
            "reason": "expired_daily_cull",
            "items": [
                {
                    "sku": "tomato",
                    "qty_delta": -40,
                    "batch_id": "BATCH-TOMATO-20250203",
                    "unit_cost": 330,
                }
            ],
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "QC Agent 执行过期蔬菜自动报损")

    # Q1 refund for live fish quality dissatisfaction.
    q1_refund_hash = sha256(b"refund-q1-fish-20250204").hexdigest()
    r = _append(
        session,
        actor=refund_agent,
        event_type="RefundIssued",
        occurred_at=_ts("2025-02-04T20:10:00Z"),
        payload={
            "order_id": "ORD-20250118-01",
            "amount": 10800,
            "receipt_hash": q1_refund_hash,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "reason": "fish_quality_dissatisfaction"},
    )
    remember(r, "Refund Agent 处理活鱼退款")
    add_bank_tx(
        tx_id="OUT-REFUND-20250204-01",
        occurred_at=_ts("2025-02-04T20:10:00Z"),
        direction="out",
        counterparty="C002:Brian Wang",
        amount_cents=10800,
        subject="refund",
        actor=refund_agent,
        reference="ORD-20250118-01",
    )

    # Q2 incident and compensation (similar to public conflict narrative).
    r = _append(
        session,
        actor=complaint_agent,
        event_type="ComplaintLogged",
        occurred_at=_ts("2025-05-22T10:05:00Z"),
        payload={
            "complaint_id": "CMP-20250522-001",
            "order_id": "ORD-20250521-01",
            "customer_ref": "C006:Fiona Xu",
            "topic": "service_attitude_conflict",
            "severity": "high",
            "summary": "现场收银等待引发争执，触发投诉流程",
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Complaint Agent 记录顾客投诉")

    r = _append(
        session,
        actor=complaint_agent,
        event_type="CustomerConflictReported",
        occurred_at=_ts("2025-05-22T10:20:00Z"),
        payload={
            "conflict_id": "CONFLICT-20250522-01",
            "order_id": "ORD-20250521-01",
            "customer_ref": "C006:Fiona Xu",
            "employee_ref": "EMP-CASHIER-07",
            "severity": "high",
            "resolution": "human_mediation_and_compensation",
            "privacy_tags": ["customer_pii", "employee_pii"],
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID},
    )
    remember(r, "Complaint Agent 上报顾客与员工冲突")

    comp_receipt = _capture_compensation_receipt(
        session=session,
        receipt_store=receipt_store,
        actor=human,
        conflict_id="CONFLICT-20250522-01",
        order_id="ORD-20250521-01",
        amount=68000,
        occurred_at=_ts("2025-05-22T11:00:00Z"),
    )
    r = _append(
        session,
        actor=human,
        event_type="CompanyCompensationIssued",
        occurred_at=_ts("2025-05-22T11:00:00Z"),
        payload={
            "conflict_id": "CONFLICT-20250522-01",
            "order_id": "ORD-20250521-01",
            "amount": 68000,
            "reason": "offline_mediation_resolution",
            "receipt_hash": comp_receipt.receipt_hash,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "offline_resolution": True},
    )
    remember(r, "Human 线下调解并支付赔偿")
    add_bank_tx(
        tx_id="OUT-COMP-20250522-01",
        occurred_at=_ts("2025-05-22T11:00:00Z"),
        direction="out",
        counterparty="C006:Fiona Xu",
        amount_cents=68000,
        subject="conflict_compensation",
        actor=human,
        reference="CONFLICT-20250522-01",
    )

    # Q2 abnormal refund to trigger selective disclosure use case.
    q2_refund_hash = sha256(b"refund-q2-abnormal-20250624").hexdigest()
    r = _append(
        session,
        actor=refund_agent,
        event_type="RefundIssued",
        occurred_at=_ts("2025-06-24T18:10:00Z"),
        payload={
            "order_id": "ORD-20250622-01",
            "amount": 45000,
            "receipt_hash": q2_refund_hash,
        },
        tool_trace={"scenario_id": DEFAULT_SCENARIO_ID, "reason": "abnormal_refund_case"},
    )
    remember(r, "Refund Agent 处理大额异常退款")
    add_bank_tx(
        tx_id="OUT-REFUND-20250624-01",
        occurred_at=_ts("2025-06-24T18:10:00Z"),
        direction="out",
        counterparty="C004:Daniel Chen",
        amount_cents=45000,
        subject="abnormal_refund",
        actor=refund_agent,
        reference="ORD-20250622-01",
    )

    # High-risk bank transfer actions handled by human.
    r = _log_tool_invocation(
        session,
        actor=human,
        connector_name="payment",
        action="bank_transfer",
        payload={"amount_cents": 580000, "to": "supplier-lanshan-tea", "supplier_id": "supplier-lanshan-tea", "settlement_procurement_id": "PO-20250305-TEA", "purpose": "supplier_settlement"},
        run_id="run-tsm-q1q2-bank-001",
        task_id="task-human-bank-transfer-tea",
        occurred_at=_ts("2025-03-20T09:00:00Z"),
    )
    remember(r, "Human 执行高风险银行付款（茶叶结算）")
    add_bank_tx(
        tx_id="OUT-BANK-20250320-01",
        occurred_at=_ts("2025-03-20T09:00:00Z"),
        direction="out",
        counterparty="supplier-lanshan-tea",
        amount_cents=580000,
        subject="supplier_settlement",
        actor=human,
        reference="PO-20250305-TEA",
    )

    r = _log_tool_invocation(
        session,
        actor=human,
        connector_name="payment",
        action="bank_transfer",
        payload={"amount_cents": 620000, "to": "supplier-donghai-fresh", "supplier_id": "supplier-donghai-fresh", "settlement_procurement_id": "PO-20250520-FISH", "purpose": "supplier_settlement"},
        run_id="run-tsm-q1q2-bank-002",
        task_id="task-human-bank-transfer-fish",
        occurred_at=_ts("2025-05-27T12:10:00Z"),
    )
    remember(r, "Human 执行高风险银行付款（新活鱼供应商）")
    add_bank_tx(
        tx_id="OUT-BANK-20250527-01",
        occurred_at=_ts("2025-05-27T12:10:00Z"),
        direction="out",
        counterparty="supplier-donghai-fresh",
        amount_cents=620000,
        subject="supplier_settlement",
        actor=human,
        reference="PO-20250520-FISH",
    )

    r = _log_tool_invocation(
        session,
        actor=human,
        connector_name="payment",
        action="bank_transfer",
        payload={"amount_cents": 540000, "to": "supplier-green-valley", "supplier_id": "supplier-green-valley", "settlement_procurement_id": "PO-20250603-TOMATO", "purpose": "supplier_settlement"},
        run_id="run-tsm-q1q2-bank-003",
        task_id="task-human-bank-transfer-tomato",
        occurred_at=_ts("2025-06-28T10:00:00Z"),
    )
    remember(r, "Human executed supplier settlement for tomato procurement")
    add_bank_tx(
        tx_id="OUT-BANK-20250628-01",
        occurred_at=_ts("2025-06-28T10:00:00Z"),
        direction="out",
        counterparty="supplier-green-valley",
        amount_cents=540000,
        subject="supplier_settlement",
        actor=human,
        reference="PO-20250603-TOMATO",
    )

    daily_periods = [
        (f"2025-06-{day:02d}", _ts(f"2025-06-{day:02d}T00:00:00Z"), _ts(f"2025-06-{day:02d}T00:00:00Z") + timedelta(days=1))
        for day in range(1, 31)
    ]

    weekly_periods = [
        ("2025-W14", _ts("2025-04-01T00:00:00Z"), _ts("2025-04-08T00:00:00Z")),
        ("2025-W15", _ts("2025-04-08T00:00:00Z"), _ts("2025-04-15T00:00:00Z")),
        ("2025-W16", _ts("2025-04-15T00:00:00Z"), _ts("2025-04-22T00:00:00Z")),
        ("2025-W17", _ts("2025-04-22T00:00:00Z"), _ts("2025-04-29T00:00:00Z")),
        ("2025-W18", _ts("2025-04-29T00:00:00Z"), _ts("2025-05-06T00:00:00Z")),
        ("2025-W19", _ts("2025-05-06T00:00:00Z"), _ts("2025-05-13T00:00:00Z")),
        ("2025-W20", _ts("2025-05-13T00:00:00Z"), _ts("2025-05-20T00:00:00Z")),
        ("2025-W21", _ts("2025-05-20T00:00:00Z"), _ts("2025-05-27T00:00:00Z")),
        ("2025-W22", _ts("2025-05-27T00:00:00Z"), _ts("2025-06-03T00:00:00Z")),
        ("2025-W23", _ts("2025-06-03T00:00:00Z"), _ts("2025-06-10T00:00:00Z")),
        ("2025-W24", _ts("2025-06-10T00:00:00Z"), _ts("2025-06-17T00:00:00Z")),
        ("2025-W25", _ts("2025-06-17T00:00:00Z"), _ts("2025-06-24T00:00:00Z")),
        ("2025-W26", _ts("2025-06-24T00:00:00Z"), _ts("2025-07-01T00:00:00Z")),
    ]

    monthly_periods = [
        ("2025-01", _ts("2025-01-01T00:00:00Z"), _ts("2025-02-01T00:00:00Z")),
        ("2025-02", _ts("2025-02-01T00:00:00Z"), _ts("2025-03-01T00:00:00Z")),
        ("2025-03", _ts("2025-03-01T00:00:00Z"), _ts("2025-04-01T00:00:00Z")),
        ("2025-04", _ts("2025-04-01T00:00:00Z"), _ts("2025-05-01T00:00:00Z")),
        ("2025-05", _ts("2025-05-01T00:00:00Z"), _ts("2025-06-01T00:00:00Z")),
        ("2025-06", _ts("2025-06-01T00:00:00Z"), _ts("2025-07-01T00:00:00Z")),
    ]

    public_daily_runs: list[dict[str, Any]] = []
    public_weekly_runs: list[dict[str, Any]] = []
    public_monthly_runs: list[dict[str, Any]] = []
    investor_weekly_runs: list[dict[str, Any]] = []
    investor_monthly_runs: list[dict[str, Any]] = []

    for label, start, end in daily_periods:
        public_run = publish_disclosure_run(
            session=session,
            policy_id="policy_public_v1",
            period_start=start,
            period_end=end,
            group_by=["store_id", "region", "time_slot", "promotion_phase", "category"],
            actor=ceo,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        row = _find_disclosure_publish_event(session, public_run.disclosure_id)
        if row is not None:
            remember(row, f"Published public daily disclosure: {label}")
        public_daily_runs.append({"label": label, **_minimal_disclosure(public_run.payload)})

    for label, start, end in weekly_periods:
        public_run = publish_disclosure_run(
            session=session,
            policy_id="policy_public_v1",
            period_start=start,
            period_end=end,
            group_by=["store_id", "region", "promotion_phase", "channel", "category"],
            actor=ceo,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        row = _find_disclosure_publish_event(session, public_run.disclosure_id)
        if row is not None:
            remember(row, f"Published public weekly disclosure: {label}")
        public_weekly_runs.append({"label": label, **_minimal_disclosure(public_run.payload)})

        investor_run = publish_disclosure_run(
            session=session,
            policy_id="policy_investor_v1",
            period_start=start,
            period_end=end,
            group_by=["store_id", "region", "time_slot", "promotion_phase", "channel", "category", "sku"],
            actor=ceo,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        row = _find_disclosure_publish_event(session, investor_run.disclosure_id)
        if row is not None:
            remember(row, f"Published investor weekly disclosure: {label}")
        investor_weekly_runs.append({"label": label, **_minimal_disclosure(investor_run.payload)})

    for label, start, end in monthly_periods:
        public_actor = human if label == "2025-05" else ceo
        public_run = publish_disclosure_run(
            session=session,
            policy_id="policy_public_v1",
            period_start=start,
            period_end=end,
            group_by=["store_id", "region", "promotion_phase", "category"],
            actor=public_actor,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        row = _find_disclosure_publish_event(session, public_run.disclosure_id)
        if row is not None:
            remember(row, f"Published public monthly disclosure: {label}")
        public_monthly_runs.append({"label": label, **_minimal_disclosure(public_run.payload)})

        investor_run = publish_disclosure_run(
            session=session,
            policy_id="policy_investor_v1",
            period_start=start,
            period_end=end,
            group_by=["store_id", "region", "promotion_phase", "channel", "category", "sku"],
            actor=ceo,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        row = _find_disclosure_publish_event(session, investor_run.disclosure_id)
        if row is not None:
            remember(row, f"Published investor monthly disclosure: {label}")
        investor_monthly_runs.append({"label": label, **_minimal_disclosure(investor_run.payload)})

    public_runs = public_monthly_runs
    investor_runs = investor_monthly_runs

    auditor_june = publish_disclosure_run(
        session=session,
        policy_id="policy_auditor_v1",
        period_start=_ts("2025-06-01T00:00:00Z"),
        period_end=_ts("2025-07-01T00:00:00Z"),
        group_by=["channel", "sku"],
        actor=auditor,
        scenario_id=DEFAULT_SCENARIO_ID,
    )
    row = _find_disclosure_publish_event(session, auditor_june.disclosure_id)
    if row is not None:
        remember(row, "Auditor 发布 selective-disclosure-ready 披露")

    public_disclosure = public_runs[-1]
    investor_disclosure = investor_runs[-1]

    actor_actions = {
        "agent": [x for x in action_log if x["actor"]["type"] == "agent"],
        "human": [x for x in action_log if x["actor"]["type"] == "human"],
        "auditor": [x for x in action_log if x["actor"]["type"] == "auditor"],
    }

    identity_ids: list[str] = []
    for actor_type in ("agent", "human", "auditor"):
        actions = actor_actions[actor_type]
        if actions:
            identity_ids.append(actions[0]["event_id"])
            identity_ids.append(actions[-1]["event_id"])
    identity_ids = list(dict.fromkeys(identity_ids))

    soul_manifest = _collect_soul_manifest()
    soul_manifest_hash = _sha256_json(soul_manifest)

    disclosure_ids = {
        public_disclosure["disclosure_id"],
        investor_disclosure["disclosure_id"],
        auditor_june.disclosure_id,
    }
    disclosure_ids.update([x["disclosure_id"] for x in public_daily_runs])
    disclosure_ids.update([x["disclosure_id"] for x in public_weekly_runs])
    disclosure_ids.update([x["disclosure_id"] for x in public_monthly_runs])
    disclosure_ids.update([x["disclosure_id"] for x in investor_weekly_runs])
    disclosure_ids.update([x["disclosure_id"] for x in investor_monthly_runs])

    exports = _export_story_artifacts(
        session=session,
        disclosure_ids=disclosure_ids,
        suppliers=SUPPLIERS,
        customers=CUSTOMERS,
        bank_transactions=bank_transactions,
        soul_manifest=soul_manifest,
    )

    period = {
        "start": _ts("2025-06-01T00:00:00Z").isoformat().replace("+00:00", "Z"),
        "end": _ts("2025-07-01T00:00:00Z").isoformat().replace("+00:00", "Z"),
    }

    result = {
        "scenario": "david_transparent_supermarket_two_quarter_story",
        "scenario_id": DEFAULT_SCENARIO_ID,
        "scenario_version": DEFAULT_SCENARIO_VERSION,
        "seeded_now": True,
        "period": period,
        "periods": [
            {
                "label": "Q1_2025",
                "start": "2025-01-01T00:00:00Z",
                "end": "2025-04-01T00:00:00Z",
            },
            {
                "label": "Q2_2025",
                "start": "2025-04-01T00:00:00Z",
                "end": "2025-07-01T00:00:00Z",
            },
        ],
        "purpose": "Demonstrate David透明超市 with dual-layer trust accounting, daily+weekly+monthly disclosures, verifiable commitments, and role-based signed operations over two quarters.",
        "company": {
            "name": "David透明超市",
            "tagline": "We do not sell products, we sell unmodified truth.",
            "soul_manifest_hash": soul_manifest_hash,
        },
        "roles": {
            "ceo_agent": {
                "name": "David",
                "birth_year": 2002,
                "origin": "Shandong",
                "education": "Beihang BS + CUHK MSc",
                "persona": "Calm, consistent, and obsessed with verifiable facts",
                "actor": ceo.model_dump(),
            },
            "human_legal": {
                "name": "Xu Dawei",
                "education": "Beihang BS + CUHK",
                "actor": human.model_dump(),
            },
            "auditor": {
                "name": "External Auditor",
                "principle": "Trust math, not people",
                "actor": auditor.model_dump(),
            },
            "sub_agents": [
                sales_agent.model_dump(),
                qc_agent.model_dump(),
                refund_agent.model_dump(),
                complaint_agent.model_dump(),
                logistics_agent.model_dump(),
            ],
        },
        "storyline": [
            "Q1: David agent drives procurement, sales, shipping, and refunds with signed events.",
            "Q2: Supplier switch, customer conflict, compensation handling, and selective disclosure review.",
            "Daily + weekly + monthly disclosures are published under policy control with Merkle commitments.",
            "Every disclosure is anchored to immudb for tamper-evident verification.",
        ],
        "partners": SUPPLIERS,
        "customers": CUSTOMERS,
        "bank_transactions": bank_transactions,
        "agent_actions": actor_actions["agent"],
        "human_actions": actor_actions["human"],
        "auditor_actions": actor_actions["auditor"],
        "receipt_anchors": receipt_anchors,
        "public_disclosure": public_disclosure,
        "investor_disclosure": investor_disclosure,
        "extra_disclosures": [
            {"label": "auditor_2025_06", **_minimal_disclosure(auditor_june.payload)},
            *public_runs[:-1],
        ],
        "public_daily_disclosures": public_daily_runs,
        "public_weekly_disclosures": public_weekly_runs,
        "public_monthly_disclosures": public_monthly_runs,
        "investor_weekly_disclosures": investor_weekly_runs,
        "investor_monthly_disclosures": investor_monthly_runs,
        "soul_manifest": soul_manifest,
        "data_exports": exports,
        "how_to_verify": {
            "proof_api": f"/disclosure/{public_disclosure['disclosure_id']}/proof?metric_key=revenue_cents",
            "anchor_api": f"/anchor/disclosure/{public_disclosure['disclosure_id']}",
            "script": "python scripts/verify_disclosure.py --base-url http://localhost:8000 --disclosure-id <id> --metric-key revenue_cents",
            "selective_disclosure": f"/disclosure/{auditor_june.disclosure_id}/selective/request",
        },
        "superset": {
            "url": "http://localhost:8088",
            "dashboard_url": "http://localhost:8088/superset/dashboard/david-transparent-supermarket-story/",
            "username": "admin",
            "password": "admin",
            "recommended_datasets": [
                "public.disclosure_public_daily_kpi_pretty",
                "public.disclosure_public_weekly_kpi_pretty",
                "public.disclosure_public_monthly_kpi_pretty",
                "public.disclosure_investor_revenue_dimension_pretty",
                "public.disclosure_investor_supplier_term_pretty",
            ],
            "recommended_charts": [
                "Daily Revenue Trend (CNY)",
                "Daily Net Operating Cashflow (CNY)",
                "Daily Average Order Value (CNY)",
                "Weekly Repeat Purchase Rate (%)",
                "Weekly QC Fail Rate (%)",
                "Weekly Complaint Resolution Hours",
                "Monthly Inventory Turnover Days",
                "Monthly Slow-moving SKU Ratio (%)",
                "Promotion Phase Revenue Mix (CNY)",
                "Supplier Payment Term Structure (CNY)",
            ],
        },
    }

    _refresh_superset_recommendations(result)
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

    return _apply_public_detail_level(result, detail_level)


def get_default_scenario_story(session: Session, detail_level: str = "summary") -> dict[str, Any]:
    marker = _marker_event(session)
    if marker is None:
        return seed_default_scenario(session, detail_level=detail_level)
    return _build_existing_response(session, marker, detail_level=detail_level)
