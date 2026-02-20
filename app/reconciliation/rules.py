from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.persistence.models import LedgerEventModel


@dataclass
class ReconciliationResult:
    rule: str
    passed: bool
    detail: str


def check_payment_equals_revenue(
    events: Iterable[LedgerEventModel],
    disclosed_revenue_cents: int,
) -> ReconciliationResult:
    payments = sum(
        int(event.payload.get("amount", 0))
        for event in events
        if event.event_type == "PaymentCaptured"
    )
    passed = payments == disclosed_revenue_cents
    return ReconciliationResult(
        rule="payment_equals_revenue",
        passed=passed,
        detail=f"payments={payments}, disclosed_revenue={disclosed_revenue_cents}",
    )


def check_inventory_non_negative(events: Iterable[LedgerEventModel]) -> ReconciliationResult:
    balances: dict[str, int] = {}
    for event in events:
        if event.event_type == "GoodsReceived" and event.payload.get("qc_passed", False):
            for item in event.payload.get("items", []):
                balances[item["sku"]] = balances.get(item["sku"], 0) + int(item["qty"])

        if event.event_type == "ShipmentDispatched":
            for item in event.payload.get("items", []):
                sku = item["sku"]
                balances[sku] = balances.get(sku, 0) - int(item["qty"])
                if balances[sku] < 0:
                    return ReconciliationResult(
                        rule="inventory_non_negative",
                        passed=False,
                        detail=f"negative inventory for sku={sku}",
                    )

        if event.event_type == "InventoryAdjusted":
            for item in event.payload.get("items", []):
                sku = item["sku"]
                balances[sku] = balances.get(sku, 0) + int(item["qty_delta"])
                if balances[sku] < 0:
                    return ReconciliationResult(
                        rule="inventory_non_negative",
                        passed=False,
                        detail=f"negative inventory after adjustment sku={sku}",
                    )

    return ReconciliationResult(rule="inventory_non_negative", passed=True, detail="ok")


def check_refund_posting_exists(pnl_report: dict, events: Iterable[LedgerEventModel]) -> ReconciliationResult:
    refund_events = [event for event in events if event.event_type == "RefundIssued"]
    if not refund_events:
        return ReconciliationResult(rule="refund_posting_exists", passed=True, detail="no refunds")

    refund_total = sum(int(event.payload.get("amount", 0)) for event in refund_events)
    posted_total = int(pnl_report.get("refunds", 0))
    passed = refund_total == posted_total
    return ReconciliationResult(
        rule="refund_posting_exists",
        passed=passed,
        detail=f"refund_events={refund_total}, posted_refunds={posted_total}",
    )


def run_minimum_reconciliation(events: Iterable[LedgerEventModel], disclosed_revenue_cents: int, pnl_report: dict) -> list[ReconciliationResult]:
    event_list = list(events)
    return [
        check_payment_equals_revenue(event_list, disclosed_revenue_cents=disclosed_revenue_cents),
        check_inventory_non_negative(event_list),
        check_refund_posting_exists(pnl_report, event_list),
    ]
