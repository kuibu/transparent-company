from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def _json_type():
    return JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class Base(DeclarativeBase):
    pass


class LedgerEventModel(Base):
    __tablename__ = "ledger_events"

    seq_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    tool_trace: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)


class OrderViewModel(Base):
    __tablename__ = "orders_view"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="placed", nullable=False)
    order_total_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    refunded_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    shipped_qty: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    line_items: Mapped[list] = mapped_column(_json_type(), default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InventoryViewModel(Base):
    __tablename__ = "inventory_view"
    __table_args__ = (
        UniqueConstraint("sku", "batch_id", name="uq_inventory_sku_batch"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    qty_on_hand: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expiry_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    unit_cost_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DisclosureRunModel(Base):
    __tablename__ = "disclosure_runs"

    disclosure_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    root_summary: Mapped[str] = mapped_column(String(64), nullable=False)
    root_details: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    statement_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    statement_signature: Mapped[str] = mapped_column(Text, nullable=False)
    proof_index: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    detail_index: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    anchor_ref: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DisclosureMetricModel(Base):
    __tablename__ = "disclosure_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("disclosure_runs.disclosure_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DisclosureGroupedMetricModel(Base):
    __tablename__ = "disclosure_grouped_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("disclosure_runs.disclosure_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    group_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AnchorRecordModel(Base):
    __tablename__ = "anchor_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    value_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SelectiveRevealAuditModel(Base):
    __tablename__ = "selective_reveal_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disclosure_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    challenge_subject: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_group: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_ledger_events_occurred_at", LedgerEventModel.occurred_at)
Index("ix_ledger_events_event_type", LedgerEventModel.event_type)
Index("ix_disclosure_metric_key", DisclosureMetricModel.metric_key)
Index("ix_disclosure_grouped_metric_key", DisclosureGroupedMetricModel.metric_key)
