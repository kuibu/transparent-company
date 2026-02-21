from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.ledger.canonical import sha256_hex


Audience = Literal["public", "investor", "partner", "auditor"]
Granularity = Literal["hour", "day", "week", "month"]
ProofLevel = Literal["root_only", "root_plus_proof", "selective_disclosure_ready"]


class RedactionRules(BaseModel):
    hide_customer_ref: bool = True
    hide_supplier_id: bool = True
    hide_unit_cost: bool = True
    allow_sku: bool = False


class DisclosurePolicy(BaseModel):
    policy_id: str
    version: str
    audience: Audience
    time_granularity: Granularity
    allowed_metrics: list[str]
    allowed_group_by: list[str]
    redaction: RedactionRules
    delay_days: int = Field(ge=0)
    proof_level: ProofLevel

    def policy_hash(self) -> str:
        return sha256_hex(self.model_dump())


COMMON_GROUP_BY = [
    "channel",
    "region",
    "category",
    "store_id",
    "time_slot",
    "promotion_phase",
]

COMMON_METRICS = [
    "revenue_cents",
    "refunds_cents",
    "compensation_cents",
    "net_revenue_cents",
    "orders_count",
    "shipment_qty",
    "refund_rate_bps",
    "conflict_count",
    "conflict_rate_bps",
    "inventory_loss_rate_bps",
    "gross_margin_bps",
    "avg_order_value_cents",
    "repeat_purchase_rate_bps",
    "slow_moving_sku_ratio_bps",
    "complaint_resolution_hours_avg",
    "compensation_ratio_bps",
    "qc_fail_rate_bps",
    "operating_cash_net_inflow_cents",
    "supplier_settlement_cents",
    "supplier_payment_term_days_avg",
    "supplier_term_short_ratio_bps",
    "supplier_term_mid_ratio_bps",
    "supplier_term_long_ratio_bps",
]


DEFAULT_POLICIES: dict[str, DisclosurePolicy] = {
    "policy_public_v1": DisclosurePolicy(
        policy_id="policy_public_v1",
        version="1.2.0",
        audience="public",
        time_granularity="day",
        allowed_metrics=COMMON_METRICS,
        allowed_group_by=COMMON_GROUP_BY,
        redaction=RedactionRules(hide_customer_ref=True, hide_supplier_id=True, hide_unit_cost=True, allow_sku=False),
        delay_days=1,
        proof_level="root_plus_proof",
    ),
    "policy_investor_v1": DisclosurePolicy(
        policy_id="policy_investor_v1",
        version="1.2.0",
        audience="investor",
        time_granularity="day",
        allowed_metrics=COMMON_METRICS + ["gross_profit_cents", "inventory_turnover_days", "cogs_cents"],
        allowed_group_by=COMMON_GROUP_BY + ["sku", "promotion_id"],
        redaction=RedactionRules(hide_customer_ref=True, hide_supplier_id=True, hide_unit_cost=True, allow_sku=True),
        delay_days=0,
        proof_level="root_plus_proof",
    ),
    "policy_auditor_v1": DisclosurePolicy(
        policy_id="policy_auditor_v1",
        version="1.2.0",
        audience="auditor",
        time_granularity="day",
        allowed_metrics=COMMON_METRICS + ["gross_profit_cents", "inventory_turnover_days", "cogs_cents"],
        allowed_group_by=COMMON_GROUP_BY + ["sku", "promotion_id"],
        redaction=RedactionRules(hide_customer_ref=True, hide_supplier_id=True, hide_unit_cost=False, allow_sku=True),
        delay_days=0,
        proof_level="selective_disclosure_ready",
    ),
}


def list_policies() -> list[DisclosurePolicy]:
    return list(DEFAULT_POLICIES.values())


def get_policy(policy_id: str) -> DisclosurePolicy:
    policy = DEFAULT_POLICIES.get(policy_id)
    if not policy:
        raise KeyError(f"unknown policy_id: {policy_id}")
    return policy
