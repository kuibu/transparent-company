from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from app.persistence.models import LedgerEventModel


@dataclass
class PostingRecord:
    date: datetime
    narration: str
    debit_account: str
    credit_account: str
    amount_cents: int
    meta: dict


def cents_to_amount(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)


def events_to_postings(
    events: Iterable[LedgerEventModel],
    shipment_costs: dict[str, int] | None = None,
) -> list[PostingRecord]:
    shipment_costs = shipment_costs or {}
    rows: list[PostingRecord] = []

    for event in events:
        p = event.payload
        if event.event_type == "GoodsReceived" and p.get("qc_passed", False):
            total_cost = 0
            for item in p.get("items", []):
                unit_cost = int(item.get("unit_cost") or 0)
                total_cost += int(item["qty"]) * unit_cost
            rows.append(
                PostingRecord(
                    date=event.occurred_at,
                    narration=f"GoodsReceived {p.get('procurement_id')}",
                    debit_account="Assets:Inventory",
                    credit_account="Assets:Cash",
                    amount_cents=total_cost,
                    meta={"event_id": event.event_id, "event_type": event.event_type},
                )
            )

        if event.event_type == "PaymentCaptured":
            rows.append(
                PostingRecord(
                    date=event.occurred_at,
                    narration=f"PaymentCaptured {p.get('order_id')}",
                    debit_account="Assets:Cash",
                    credit_account="Income:Sales",
                    amount_cents=int(p.get("amount", 0)),
                    meta={"event_id": event.event_id, "event_type": event.event_type},
                )
            )

        if event.event_type == "ShipmentDispatched":
            cogs = int(shipment_costs.get(event.event_id, 0))
            rows.append(
                PostingRecord(
                    date=event.occurred_at,
                    narration=f"ShipmentDispatched {p.get('order_id')}",
                    debit_account="Expenses:COGS",
                    credit_account="Assets:Inventory",
                    amount_cents=cogs,
                    meta={"event_id": event.event_id, "event_type": event.event_type},
                )
            )

        if event.event_type == "RefundIssued":
            rows.append(
                PostingRecord(
                    date=event.occurred_at,
                    narration=f"RefundIssued {p.get('order_id')}",
                    debit_account="Expenses:Refunds",
                    credit_account="Assets:Cash",
                    amount_cents=int(p.get("amount", 0)),
                    meta={"event_id": event.event_id, "event_type": event.event_type},
                )
            )

    return rows


def postings_to_beancount_text(postings: list[PostingRecord]) -> str:
    lines = [
        'option "title" "Transparent Company Ledger"',
        'option "operating_currency" "CNY"',
    ]
    if postings:
        first_day = min(posting.date.date() for posting in postings)
    else:
        first_day = datetime.utcnow().date()

    opens = [
        "Assets:Cash",
        "Assets:Inventory",
        "Income:Sales",
        "Expenses:COGS",
        "Expenses:Refunds",
    ]
    for account in opens:
        lines.append(f"{first_day.isoformat()} open {account} CNY")

    for posting in postings:
        day = posting.date.date().isoformat()
        amount = cents_to_amount(posting.amount_cents)
        lines.append(f'{day} * "{posting.narration}"')
        lines.append(f"  {posting.debit_account}  {amount} CNY")
        lines.append(f"  {posting.credit_account}  {-amount} CNY")

    return "\n".join(lines) + "\n"
