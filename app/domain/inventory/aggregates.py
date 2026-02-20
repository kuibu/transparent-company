from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InventoryLot:
    sku: str
    batch_id: str
    qty: int
    unit_cost: int
    expiry_date: str | None = None


@dataclass
class InventoryAggregate:
    lots: dict[str, list[InventoryLot]] = field(default_factory=dict)

    def add_lot(self, lot: InventoryLot) -> None:
        bucket = self.lots.setdefault(lot.sku, [])
        bucket.append(lot)
        bucket.sort(key=lambda x: (x.expiry_date or "9999-12-31", x.batch_id))

    def qty_on_hand(self, sku: str) -> int:
        return sum(lot.qty for lot in self.lots.get(sku, []))

    def consume(self, sku: str, qty: int) -> int:
        remaining = qty
        cost = 0
        for lot in self.lots.get(sku, []):
            if remaining <= 0:
                break
            take = min(lot.qty, remaining)
            lot.qty -= take
            remaining -= take
            cost += take * lot.unit_cost
        if remaining > 0:
            raise ValueError(f"negative inventory prevented for sku={sku}, short={remaining}")
        self.lots[sku] = [lot for lot in self.lots.get(sku, []) if lot.qty > 0]
        return cost
