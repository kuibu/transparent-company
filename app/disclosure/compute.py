from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from app.disclosure.policies import DisclosurePolicy
from app.ledger.canonical import canonical_json
from app.persistence.models import LedgerEventModel


SKU_CATEGORY_MAP = {
    "tomato": "vegetable",
    "cucumber": "vegetable",
    "fish": "aquatic",
    "tea": "tea",
    "apple": "fruit",
}


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


def _sku_category(sku: str) -> str:
    return SKU_CATEGORY_MAP.get(sku, sku)


def _customer_id(customer_ref: str | None) -> str | None:
    if not customer_ref:
        return None
    return str(customer_ref).split(":", 1)[0].strip() or None


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
            "store_id": payload.get("store_id"),
            "time_slot": payload.get("time_slot"),
            "promotion_id": payload.get("promotion_id"),
            "promotion_phase": payload.get("promotion_phase"),
            "customer_id": _customer_id(payload.get("customer_ref")),
            "order_event_hash": event.event_hash,
            "order_event_id": event.event_id,
        }
        for item in payload.get("items", []):
            sku = item["sku"]
            rows.append(
                {
                    "order_id": order_id,
                    "customer_id": _customer_id(payload.get("customer_ref")),
                    "sku": sku,
                    "category": _sku_category(sku),
                    "qty": int(item["qty"]),
                    "unit_price": int(item["unit_price"]),
                    "line_revenue": int(item["qty"]) * int(item["unit_price"]),
                    "channel": payload.get("channel"),
                    "region": payload.get("region"),
                    "store_id": payload.get("store_id"),
                    "time_slot": payload.get("time_slot"),
                    "promotion_id": payload.get("promotion_id"),
                    "promotion_phase": payload.get("promotion_phase"),
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


def _compensation_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "CompanyCompensationIssued":
            continue
        rows.append(
            {
                "conflict_id": event.payload.get("conflict_id"),
                "order_id": event.payload.get("order_id"),
                "amount": int(event.payload.get("amount", 0)),
                "occurred_at": _as_utc(event.occurred_at),
                "event_hash": event.event_hash,
                "event_id": event.event_id,
            }
        )
    return rows


def _conflict_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "CustomerConflictReported":
            continue
        rows.append(
            {
                "conflict_id": event.payload.get("conflict_id"),
                "order_id": event.payload.get("order_id"),
                "severity": event.payload.get("severity"),
                "event_hash": event.event_hash,
                "event_id": event.event_id,
            }
        )
    return rows


def _complaint_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "ComplaintLogged":
            continue
        rows.append(
            {
                "complaint_id": event.payload.get("complaint_id"),
                "order_id": event.payload.get("order_id"),
                "occurred_at": _as_utc(event.occurred_at),
                "event_hash": event.event_hash,
                "event_id": event.event_id,
            }
        )
    return rows


def _qc_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows: list[dict] = []
    for event in events:
        if event.event_type != "GoodsReceived":
            continue
        qc_passed = bool(event.payload.get("qc_passed", False))
        for item in event.payload.get("items", []):
            rows.append(
                {
                    "procurement_id": event.payload.get("procurement_id"),
                    "sku": item.get("sku"),
                    "qty": int(item.get("qty", 0)),
                    "qc_passed": qc_passed,
                    "event_hash": event.event_hash,
                }
            )
    return rows


def _procurement_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "ProcurementOrdered":
            continue
        rows.append(
            {
                "procurement_id": event.payload.get("procurement_id"),
                "supplier_id": event.payload.get("supplier_id"),
                "occurred_at": _as_utc(event.occurred_at),
                "event_hash": event.event_hash,
            }
        )
    return rows


def _supplier_settlement_rows(events: list[LedgerEventModel]) -> list[dict]:
    rows = []
    for event in events:
        if event.event_type != "ToolInvocationLogged":
            continue
        payload = event.payload or {}
        if payload.get("connector") != "payment" or payload.get("action") != "bank_transfer":
            continue
        amount = int(payload.get("amount_cents") or 0)
        if amount <= 0:
            continue
        purpose = str(payload.get("purpose") or "")
        supplier_id = payload.get("supplier_id")
        settlement_procurement_id = payload.get("settlement_procurement_id")
        is_supplier_settlement = bool(supplier_id or settlement_procurement_id or "supplier" in purpose)
        if not is_supplier_settlement:
            continue
        rows.append(
            {
                "amount": amount,
                "supplier_id": supplier_id,
                "settlement_procurement_id": settlement_procurement_id,
                "purpose": purpose,
                "occurred_at": _as_utc(event.occurred_at),
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
            sku = item["sku"]
            rows.append(
                {
                    "order_id": order_id,
                    "sku": sku,
                    "category": _sku_category(sku),
                    "qty": int(item["qty"]),
                    "channel": meta.get("channel"),
                    "region": meta.get("region"),
                    "store_id": meta.get("store_id"),
                    "time_slot": meta.get("time_slot"),
                    "promotion_id": meta.get("promotion_id"),
                    "promotion_phase": meta.get("promotion_phase"),
                    "event_hash": event.event_hash,
                }
            )
    return rows


def _inventory_loss(events: list[LedgerEventModel]) -> tuple[int, int, int]:
    received_qty = 0
    waste_qty = 0
    waste_cents = 0

    for event in events:
        payload = event.payload
        if event.event_type == "GoodsReceived" and payload.get("qc_passed", False):
            for item in payload.get("items", []):
                received_qty += int(item.get("qty", 0))

        if event.event_type == "InventoryAdjusted":
            reason = str(payload.get("reason", "")).lower()
            if "expire" not in reason and "waste" not in reason and "loss" not in reason and "damaged" not in reason:
                continue
            for item in payload.get("items", []):
                qty_delta = int(item.get("qty_delta", 0))
                if qty_delta >= 0:
                    continue
                qty_abs = abs(qty_delta)
                waste_qty += qty_abs
                waste_cents += qty_abs * int(item.get("unit_cost") or 0)

    loss_rate_bps = int((waste_qty * 10000) / received_qty) if received_qty else 0
    return waste_qty, waste_cents, loss_rate_bps


def _inventory_snapshot(events: list[LedgerEventModel], cutoff: datetime) -> tuple[dict[str, int], dict[str, int], int]:
    qty_by_sku: dict[str, int] = {}
    avg_cost_by_sku: dict[str, int] = {}

    cutoff_utc = _as_utc(cutoff)
    sorted_events = sorted(events, key=lambda e: (_as_utc(e.occurred_at), e.seq_id))

    for event in sorted_events:
        if _as_utc(event.occurred_at) >= cutoff_utc:
            break

        if event.event_type == "GoodsReceived" and event.payload.get("qc_passed", False):
            for item in event.payload.get("items", []):
                sku = item.get("sku")
                qty = int(item.get("qty", 0))
                if qty <= 0:
                    continue
                cost = int(item.get("unit_cost") or 0)
                old_qty = qty_by_sku.get(sku, 0)
                old_avg = avg_cost_by_sku.get(sku, cost)
                new_qty = old_qty + qty
                if new_qty > 0:
                    avg_cost_by_sku[sku] = int((old_qty * old_avg + qty * cost) / new_qty)
                qty_by_sku[sku] = new_qty

        elif event.event_type == "ShipmentDispatched":
            for item in event.payload.get("items", []):
                sku = item.get("sku")
                qty = int(item.get("qty", 0))
                if qty <= 0:
                    continue
                qty_by_sku[sku] = qty_by_sku.get(sku, 0) - qty

        elif event.event_type == "InventoryAdjusted":
            for item in event.payload.get("items", []):
                sku = item.get("sku")
                qty_delta = int(item.get("qty_delta", 0))
                if qty_delta == 0:
                    continue
                if qty_delta > 0 and int(item.get("unit_cost") or 0) > 0:
                    cost = int(item.get("unit_cost"))
                    old_qty = qty_by_sku.get(sku, 0)
                    old_avg = avg_cost_by_sku.get(sku, cost)
                    new_qty = old_qty + qty_delta
                    if new_qty > 0:
                        avg_cost_by_sku[sku] = int((old_qty * old_avg + qty_delta * cost) / new_qty)
                    qty_by_sku[sku] = new_qty
                else:
                    qty_by_sku[sku] = qty_by_sku.get(sku, 0) + qty_delta

    total_value = 0
    for sku, qty in qty_by_sku.items():
        if qty <= 0:
            continue
        total_value += qty * int(avg_cost_by_sku.get(sku, 0))
    return qty_by_sku, avg_cost_by_sku, total_value


def _supplier_term_bucket(term_days: int | None) -> str:
    if term_days is None:
        return "unknown"
    if term_days <= 7:
        return "<=7_days"
    if term_days <= 14:
        return "8_to_14_days"
    return ">14_days"


def _metric_key(metric_key: str, group: dict) -> str:
    return metric_key + "|" + canonical_json(group or {}).decode("utf-8")


def _group_subset(df: pd.DataFrame, group: dict) -> pd.DataFrame:
    subset = df
    for key, val in group.items():
        subset = subset[subset[key] == val]
    return subset


def compute_disclosure(
    events: list[LedgerEventModel],
    policy: DisclosurePolicy,
    period_start: datetime,
    period_end: datetime,
    group_by: list[str] | None,
    pnl_report: dict | None = None,
) -> DisclosureComputation:
    period_start_utc = _as_utc(period_start)
    period_end_utc = _as_utc(period_end)

    scoped_events = _filter_period(events, period_start_utc, period_end_utc)
    order_lines, order_meta = _order_line_rows(scoped_events)

    lines_df = pd.DataFrame(order_lines)
    payments_df = pd.DataFrame(_payment_rows(scoped_events))
    refunds_df = pd.DataFrame(_refund_rows(scoped_events))
    compensation_df = pd.DataFrame(_compensation_rows(scoped_events))
    conflict_df = pd.DataFrame(_conflict_rows(scoped_events))
    complaint_df = pd.DataFrame(_complaint_rows(scoped_events))
    shipments_df = pd.DataFrame(_shipment_rows(scoped_events, order_meta))
    qc_df = pd.DataFrame(_qc_rows(scoped_events))
    settlement_df = pd.DataFrame(_supplier_settlement_rows(scoped_events))

    procurement_df = pd.DataFrame(_procurement_rows(events))

    revenue = int(payments_df["amount"].sum()) if not payments_df.empty else 0
    refunds = int(refunds_df["amount"].sum()) if not refunds_df.empty else 0
    compensation = int(compensation_df["amount"].sum()) if not compensation_df.empty else 0
    shipment_qty = int(shipments_df["qty"].sum()) if not shipments_df.empty else 0
    orders_count = int(lines_df["order_id"].nunique()) if not lines_df.empty else 0
    conflict_count = int(conflict_df["conflict_id"].nunique()) if not conflict_df.empty else 0

    refund_rate_bps = int((refunds * 10000) / revenue) if revenue else 0
    conflict_rate_bps = int((conflict_count * 10000) / orders_count) if orders_count else 0

    cogs = int((pnl_report or {}).get("cogs", 0))
    gross_profit = revenue - refunds - cogs
    gross_margin_bps = int((gross_profit * 10000) / revenue) if revenue else 0

    waste_qty, waste_cents, inventory_loss_rate_bps = _inventory_loss(scoped_events)

    avg_order_value_cents = int(revenue / orders_count) if orders_count else 0

    if order_meta:
        order_meta_df = pd.DataFrame(
            [{"order_id": oid, **meta} for oid, meta in order_meta.items() if meta.get("customer_id")]
        )
    else:
        order_meta_df = pd.DataFrame()

    repeat_purchase_rate_bps = 0
    if not order_meta_df.empty:
        customer_order_counts = order_meta_df.groupby("customer_id")["order_id"].nunique()
        total_customers = int(customer_order_counts.shape[0])
        repeat_customers = int((customer_order_counts >= 2).sum())
        repeat_purchase_rate_bps = int((repeat_customers * 10000) / total_customers) if total_customers else 0

    opening_qty, _, opening_inventory_cents = _inventory_snapshot(events, period_start_utc)
    closing_qty, _, closing_inventory_cents = _inventory_snapshot(events, period_end_utc)
    average_inventory_cents = int((opening_inventory_cents + closing_inventory_cents) / 2)
    period_days = max(1, int((period_end_utc - period_start_utc).total_seconds() // 86400))
    inventory_turnover_days = int((period_days * average_inventory_cents) / cogs) if cogs else 0

    shipped_by_sku: dict[str, int] = {}
    if not shipments_df.empty:
        for sku, qty in shipments_df.groupby("sku")["qty"].sum().items():
            shipped_by_sku[str(sku)] = int(qty)

    inventory_skus = [sku for sku, qty in closing_qty.items() if int(qty) > 0]
    slow_skus = [sku for sku in inventory_skus if shipped_by_sku.get(sku, 0) == 0]
    slow_moving_sku_ratio_bps = int((len(slow_skus) * 10000) / len(inventory_skus)) if inventory_skus else 0

    complaint_resolution_hours_avg = 0
    if not complaint_df.empty and not compensation_df.empty:
        durations: list[float] = []
        comp_by_order = {
            row["order_id"]: _as_utc(row["occurred_at"])
            for _, row in compensation_df.sort_values("occurred_at").iterrows()
            if row.get("order_id")
        }
        for _, row in complaint_df.iterrows():
            order_id = row.get("order_id")
            complaint_at = _as_utc(row.get("occurred_at"))
            resolved_at = comp_by_order.get(order_id)
            if resolved_at and resolved_at >= complaint_at:
                durations.append((resolved_at - complaint_at).total_seconds() / 3600.0)
        if durations:
            complaint_resolution_hours_avg = int(sum(durations) / len(durations))

    compensation_ratio_bps = int((compensation * 10000) / revenue) if revenue else 0

    qc_fail_rate_bps = 0
    if not qc_df.empty:
        total_qc_qty = int(qc_df["qty"].sum())
        failed_qc_qty = int(qc_df[qc_df["qc_passed"] == False]["qty"].sum())  # noqa: E712
        qc_fail_rate_bps = int((failed_qc_qty * 10000) / total_qc_qty) if total_qc_qty else 0

    supplier_settlement_cents = int(settlement_df["amount"].sum()) if not settlement_df.empty else 0
    operating_cash_net_inflow_cents = revenue - refunds - compensation - supplier_settlement_cents

    supplier_payment_term_days_avg = 0
    supplier_term_short_ratio_bps = 0
    supplier_term_mid_ratio_bps = 0
    supplier_term_long_ratio_bps = 0
    settlement_bucket_amounts: dict[str, int] = {"<=7_days": 0, "8_to_14_days": 0, ">14_days": 0, "unknown": 0}
    settlement_bucket_hashes: dict[str, list[str]] = {"<=7_days": [], "8_to_14_days": [], ">14_days": [], "unknown": []}

    procurement_time_by_id: dict[str, datetime] = {}
    if not procurement_df.empty:
        procurement_time_by_id = {
            str(row["procurement_id"]): _as_utc(row["occurred_at"])
            for _, row in procurement_df.iterrows()
            if row.get("procurement_id")
        }

    if not settlement_df.empty:
        term_days_list: list[int] = []
        for _, row in settlement_df.iterrows():
            amount = int(row["amount"])
            settlement_ref = row.get("settlement_procurement_id")
            settlement_at = _as_utc(row.get("occurred_at"))
            term_days: int | None = None
            if settlement_ref and settlement_ref in procurement_time_by_id:
                term_days = int((settlement_at - procurement_time_by_id[settlement_ref]).total_seconds() // 86400)
                term_days_list.append(term_days)
            bucket = _supplier_term_bucket(term_days)
            settlement_bucket_amounts[bucket] = settlement_bucket_amounts.get(bucket, 0) + amount
            settlement_bucket_hashes.setdefault(bucket, []).append(str(row.get("event_hash")))

        if term_days_list:
            supplier_payment_term_days_avg = int(sum(term_days_list) / len(term_days_list))

        known_total = (
            settlement_bucket_amounts.get("<=7_days", 0)
            + settlement_bucket_amounts.get("8_to_14_days", 0)
            + settlement_bucket_amounts.get(">14_days", 0)
        )
        if known_total > 0:
            supplier_term_short_ratio_bps = int((settlement_bucket_amounts.get("<=7_days", 0) * 10000) / known_total)
            supplier_term_mid_ratio_bps = int((settlement_bucket_amounts.get("8_to_14_days", 0) * 10000) / known_total)
            supplier_term_long_ratio_bps = int((settlement_bucket_amounts.get(">14_days", 0) * 10000) / known_total)

    all_metrics = {
        "revenue_cents": revenue,
        "refunds_cents": refunds,
        "compensation_cents": compensation,
        "net_revenue_cents": revenue - refunds - compensation,
        "orders_count": orders_count,
        "shipment_qty": shipment_qty,
        "refund_rate_bps": refund_rate_bps,
        "conflict_count": conflict_count,
        "conflict_rate_bps": conflict_rate_bps,
        "inventory_waste_qty": waste_qty,
        "inventory_waste_cents": waste_cents,
        "inventory_loss_rate_bps": inventory_loss_rate_bps,
        "cogs_cents": cogs,
        "gross_profit_cents": gross_profit,
        "gross_margin_bps": gross_margin_bps,
        "avg_order_value_cents": avg_order_value_cents,
        "repeat_purchase_rate_bps": repeat_purchase_rate_bps,
        "inventory_turnover_days": inventory_turnover_days,
        "slow_moving_sku_ratio_bps": slow_moving_sku_ratio_bps,
        "complaint_resolution_hours_avg": complaint_resolution_hours_avg,
        "compensation_ratio_bps": compensation_ratio_bps,
        "qc_fail_rate_bps": qc_fail_rate_bps,
        "operating_cash_net_inflow_cents": operating_cash_net_inflow_cents,
        "supplier_settlement_cents": supplier_settlement_cents,
        "supplier_payment_term_days_avg": supplier_payment_term_days_avg,
        "supplier_term_short_ratio_bps": supplier_term_short_ratio_bps,
        "supplier_term_mid_ratio_bps": supplier_term_mid_ratio_bps,
        "supplier_term_long_ratio_bps": supplier_term_long_ratio_bps,
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
                subset = _group_subset(lines_df, group)
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
                subset = _group_subset(shipments_df, group)
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
                lambda r: 0
                if int(r["order_revenue"]) == 0
                else int((int(r["line_revenue"]) * int(r["refund_amount"])) / int(r["order_revenue"])),
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

        if not conflict_df.empty and not lines_df.empty and "conflict_count" in policy.allowed_metrics:
            conflict_lines = conflict_df.merge(lines_df[["order_id", *group_by]], on="order_id", how="left")
            conflict_groups = (
                conflict_lines.groupby(group_by, dropna=False, as_index=False)["conflict_id"]
                .nunique()
                .rename(columns={"conflict_id": "value"})
            )
            for _, row in conflict_groups.iterrows():
                group = {k: (None if pd.isna(row[k]) else row[k]) for k in group_by}
                value = int(row["value"])
                grouped_metrics.append({"metric_key": "conflict_count", "group": group, "value": value})
                subset = _group_subset(conflict_lines, group)
                detail_event_map[_metric_key("conflict_count", group)] = sorted(
                    subset["event_hash"].dropna().unique().tolist()
                )

        if not compensation_df.empty and not lines_df.empty and "compensation_cents" in policy.allowed_metrics:
            order_revenue = lines_df.groupby("order_id", as_index=False)["line_revenue"].sum().rename(
                columns={"line_revenue": "order_revenue"}
            )
            merged = lines_df.merge(order_revenue, on="order_id", how="left")
            comp_per_order = compensation_df.groupby("order_id", as_index=False)["amount"].sum().rename(
                columns={"amount": "comp_amount"}
            )
            merged = merged.merge(comp_per_order, on="order_id", how="left").fillna({"comp_amount": 0})
            merged["allocated_comp"] = merged.apply(
                lambda r: 0
                if int(r["order_revenue"]) == 0
                else int((int(r["line_revenue"]) * int(r["comp_amount"])) / int(r["order_revenue"])),
                axis=1,
            )
            comp_groups = (
                merged.groupby(group_by, dropna=False, as_index=False)["allocated_comp"].sum().rename(
                    columns={"allocated_comp": "value"}
                )
            )
            for _, row in comp_groups.iterrows():
                group = {k: (None if pd.isna(row[k]) else row[k]) for k in group_by}
                value = int(row["value"])
                grouped_metrics.append({"metric_key": "compensation_cents", "group": group, "value": value})
                detail_event_map[_metric_key("compensation_cents", group)] = sorted(
                    compensation_df["event_hash"].tolist()
                )

    if "supplier_settlement_cents" in policy.allowed_metrics:
        for bucket, amount in settlement_bucket_amounts.items():
            if amount <= 0:
                continue
            group = {"payment_term_bucket": bucket}
            grouped_metrics.append({"metric_key": "supplier_settlement_cents", "group": group, "value": int(amount)})
            detail_event_map[_metric_key("supplier_settlement_cents", group)] = sorted(
                settlement_bucket_hashes.get(bucket, [])
            )

    for metric_key, _value in metrics.items():
        detail_event_map.setdefault(_metric_key(metric_key, {}), sorted([event.event_hash for event in scoped_events]))

    grouped_metrics = [gm for gm in grouped_metrics if gm["metric_key"] in policy.allowed_metrics]

    return DisclosureComputation(
        metrics=metrics,
        grouped_metrics=grouped_metrics,
        detail_event_map=detail_event_map,
    )
