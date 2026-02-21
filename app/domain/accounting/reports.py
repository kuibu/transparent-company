from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from beancount.loader import load_string

from app.domain.accounting.postings import events_to_postings, postings_to_beancount_text
from app.persistence.models import LedgerEventModel


def _sum_account(entries, prefix: str) -> Decimal:
    total = Decimal("0")
    for entry in entries:
        if getattr(entry, "postings", None) is None:
            continue
        for posting in entry.postings:
            if posting.account == prefix:
                total += posting.units.number
    return total


def generate_pnl(
    events: Iterable[LedgerEventModel],
    shipment_costs: dict[str, int] | None = None,
) -> dict:
    postings = events_to_postings(events, shipment_costs=shipment_costs)
    ledger_text = postings_to_beancount_text(postings)
    entries, errors, _ = load_string(ledger_text)
    if errors:
        raise ValueError(f"beancount parse errors: {errors}")

    income_sales = -_sum_account(entries, "Income:Sales")
    cogs = _sum_account(entries, "Expenses:COGS")
    refunds = _sum_account(entries, "Expenses:Refunds")
    compensation = _sum_account(entries, "Expenses:Compensation")
    net_profit = income_sales - cogs - refunds - compensation

    return {
        "income_sales": int(income_sales * 100),
        "cogs": int(cogs * 100),
        "refunds": int(refunds * 100),
        "compensation": int(compensation * 100),
        "net_profit": int(net_profit * 100),
        "posting_count": len(postings),
        "beancount_ledger": ledger_text,
    }
