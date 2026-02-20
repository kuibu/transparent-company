from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderLine:
    sku: str
    qty: int
    unit_price: int


@dataclass
class OrderAggregate:
    order_id: str
    customer_ref: str
    channel: str
    region: str | None
    items: list[OrderLine] = field(default_factory=list)
    status: str = "placed"
    paid_cents: int = 0
    refunded_cents: int = 0
    shipped_qty: int = 0

    @property
    def total_cents(self) -> int:
        return sum(item.qty * item.unit_price for item in self.items)
