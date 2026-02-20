from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from app.disclosure.policies import DisclosurePolicy
from app.ledger.canonical import canonical_json
from app.persistence.models import LedgerEventModel


@dataclass
class DisclosureComputation:
    metrics: dict[str, int]
    grouped_metrics: list[dict]
    detail_event_map: dict[str, list[str]]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _filter_period(events: list[LedgerEventModel], period_start: datetime, period_end: datetime) -> list[LedgerEventModel]:
    start_utc = _as_utc(period_start)
    end_utc = _as_utc(period_end)
    return [event for event in events if start_utc <= _as_utc(event.occurred_at) < end_utc]


def _order_line_rows(events: list[LedgerEventModel]) -> tuple[list[dict], dict[str, dict]]:
    rows: list[dict] = []
    order_meta: dict[str, dict] = {}
    for event in events:
        if event.event_type != "OrderPlaced":
            continue
        payload = event.payload
        order_id = payload["order_id"]
        order_meta[order_id] = {
            "channel": payload.get("channel"),
            "region": payload.get("region"),
            "order_event_hash": event.event_hash,
            "order_event_id": event.event_id,
        }
        for item in payload.get("items", []):
            rows.append(
                {
                    "order_id": order_id,
                    "sku": item["sku"],
                    "qty": int(item["qty"]),
                    "unit_price": int(item["unit_price"]),
                    "line_revenue": int(item["qty"]) * int(item["unit_price"]),
                    "channel": payload.get("channel"),
                    "region": payload.get("region"),
                    "source_event_hash": event.event_hash,
                }
            )
    return rows, order_meta


def _payment_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "PaymentCaptured":
            continue
        rows.append(
            {
                "order_id": event.payload["order_id"],
                "amount": int(event.payload.get("amount", 0)),
                "event_hash": event.event_hash,
                "event_id": event.event_id,
            }
        )
    return rows


def _refund_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "RefundIssued":
            continue
        rows.append(
            {
                "order_id": event.payload["order_id"],
                "amount": int(event.payload.get("amount", 0)),
                "event_hash": event.event_hash,
                "event_id": event.event_id,
            }
        )
    return rows


def _shipment_rows(events: list[LedgerEventModel], order_meta: dict[str, dict]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "ShipmentDispatched":
            continue
        order_id = event.payload.get("order_id")
        meta = order_meta.get(order_id, {})
        for item in event.payload.get("items", []):
            rows.append(
                {
                    "order_id": order_id,
                    "sku": item["sku"],
                    "qty": int(item["qty"]),
                    "channel": meta.get("channel"),
                    "region": meta.get("region"),
                    "event_hash": event.event_hash,
                }
            )
    return rows


def _metric_key(metric_key: str, group: dict) -> str:
    return metric_key + "|" + canonical_json(group or {}).decode("utf-8")


def compute_disclosure(
    events: list[LedgerEventModel],
    policy: DisclosurePolicy,
    period_start: datetime,
    period_end: datetime,
    group_by: list[str] | None,
    pnl_report: dict | None = None,
) -> DisclosureComputation:
    scoped_events = _filter_period(events, period_start, period_end)
    order_lines, order_meta = _order_line_rows(scoped_events)

    lines_df = pd.DataFrame(order_lines)
    payments_df = pd.DataFrame(_payment_rows(scoped_events))
    refunds_df = pd.DataFrame(_refund_rows(scoped_events))
    shipments_df = pd.DataFrame(_shipment_rows(scoped_events, order_meta))

    revenue = int(payments_df["amount"].sum()) if not payments_df.empty else 0
    refunds = int(refunds_df["amount"].sum()) if not refunds_df.empty else 0
    shipment_qty = int(shipments_df["qty"].sum()) if not shipments_df.empty else 0
    orders_count = int(lines_df["order_id"].nunique()) if not lines_df.empty else 0
    refund_rate_bps = int((refunds * 10000) / revenue) if revenue else 0

    cogs = int((pnl_report or {}).get("cogs", 0))
    gross_profit = revenue - refunds - cogs

    all_metrics = {
        "revenue_cents": revenue,
        "refunds_cents": refunds,
        "net_revenue_cents": revenue - refunds,
        "orders_count": orders_count,
        "shipment_qty": shipment_qty,
        "refund_rate_bps": refund_rate_bps,
        "cogs_cents": cogs,
        "gross_profit_cents": gross_profit,
    }

    metrics = {k: int(v) for k, v in all_metrics.items() if k in policy.allowed_metrics}

    grouped_metrics: list[dict] = []
    detail_event_map: dict[str, list[str]] = {}

    if group_by:
        forbidden = [g for g in group_by if g not in policy.allowed_group_by]
        if forbidden:
            raise ValueError(f"group_by not allowed by policy: {forbidden}")

        if not lines_df.empty:
            revenue_groups = (
                lines_df.groupby(group_by, dropna=False, as_index=False)["line_revenue"].sum().rename(
                    columns={"line_revenue": "value"}
                )
            )
            for _, row in revenue_groups.iterrows():
                group = {k: (None if pd.isna(row[k]) else row[k]) for k in group_by}
                value = int(row["value"])
                grouped_metrics.append({"metric_key": "revenue_cents", "group": group, "value": value})

                subset = lines_df
                for key, val in group.items():
                    subset = subset[subset[key] == val]
                detail_event_map[_metric_key("revenue_cents", group)] = sorted(
                    subset["source_event_hash"].dropna().unique().tolist()
                )

        if not shipments_df.empty and "shipment_qty" in policy.allowed_metrics:
            shipment_groups = (
                shipments_df.groupby(group_by, dropna=False, as_index=False)["qty"].sum().rename(columns={"qty": "value"})
            )
            for _, row in shipment_groups.iterrows():
                group = {k: (None if pd.isna(row[k]) else row[k]) for k in group_by}
                value = int(row["value"])
                grouped_metrics.append({"metric_key": "shipment_qty", "group": group, "value": value})
                subset = shipments_df
                for key, val in group.items():
                    subset = subset[subset[key] == val]
                detail_event_map[_metric_key("shipment_qty", group)] = sorted(
                    subset["event_hash"].dropna().unique().tolist()
                )

        if not refunds_df.empty and not lines_df.empty and "refunds_cents" in policy.allowed_metrics:
            order_revenue = lines_df.groupby("order_id", as_index=False)["line_revenue"].sum().rename(
                columns={"line_revenue": "order_revenue"}
            )
            merged = lines_df.merge(order_revenue, on="order_id", how="left")
            refund_per_order = refunds_df.groupby("order_id", as_index=False)["amount"].sum().rename(
                columns={"amount": "refund_amount"}
            )
            merged = merged.merge(refund_per_order, on="order_id", how="left").fillna({"refund_amount": 0})
            merged["allocated_refund"] = merged.apply(
                lambda r: 0 if int(r["order_revenue"]) == 0 else int((int(r["line_revenue"]) * int(r["refund_amount"])) / int(r["order_revenue"])),
                axis=1,
            )
            refund_groups = (
                merged.groupby(group_by, dropna=False, as_index=False)["allocated_refund"].sum().rename(
                    columns={"allocated_refund": "value"}
                )
            )
            for _, row in refund_groups.iterrows():
                group = {k: (None if pd.isna(row[k]) else row[k]) for k in group_by}
                value = int(row["value"])
                grouped_metrics.append({"metric_key": "refunds_cents", "group": group, "value": value})
                detail_event_map[_metric_key("refunds_cents", group)] = sorted(refunds_df["event_hash"].tolist())

    for metric_key, value in metrics.items():
        detail_event_map.setdefault(_metric_key(metric_key, {}), sorted([event.event_hash for event in scoped_events]))

    grouped_metrics = [gm for gm in grouped_metrics if gm["metric_key"] in policy.allowed_metrics]

    return DisclosureComputation(
        metrics=metrics,
        grouped_metrics=grouped_metrics,
        detail_event_map=detail_event_map,
    )
