from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActorModel(BaseModel):
    type: Literal["agent", "human", "system", "auditor"]
    id: str


class ItemCostModel(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    unit_cost: int = Field(ge=0, description="int cents")


class ItemReceivedModel(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    expiry_date: str
    unit_cost: int | None = Field(default=None, ge=0, description="int cents")


class OrderItemModel(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    unit_price: int = Field(ge=0, description="int cents")


class ShipmentItemModel(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class InventoryAdjustItemModel(BaseModel):
    sku: str
    qty_delta: int
    batch_id: str | None = None


class ProcurementOrderedPayload(BaseModel):
    procurement_id: str | None = None
    supplier_id: str
    items: list[ItemCostModel]
    expected_date: str


class GoodsReceivedPayload(BaseModel):
    procurement_id: str
    batch_id: str
    items: list[ItemReceivedModel]
    qc_passed: bool


class OrderPlacedPayload(BaseModel):
    order_id: str
    customer_ref: str
    items: list[OrderItemModel]
    channel: str
    region: str | None = None


class PaymentCapturedPayload(BaseModel):
    order_id: str
    amount: int = Field(ge=0, description="int cents")
    method: str
    receipt_object_key: str
    receipt_hash: str


class ShipmentDispatchedPayload(BaseModel):
    order_id: str
    items: list[ShipmentItemModel]
    carrier_ref: str


class RefundIssuedPayload(BaseModel):
    order_id: str
    amount: int = Field(ge=0, description="int cents")
    receipt_hash: str


class InventoryAdjustedPayload(BaseModel):
    reason: str
    items: list[InventoryAdjustItemModel]


class DisclosurePublishedPayload(BaseModel):
    disclosure_id: str
    policy_id: str
    period: dict[str, str]
    metrics: dict[str, int]
    merkle_root: str
    anchor_ref: dict[str, Any]
    statement_sig_hash: str


class SelectiveDisclosureRevealedPayload(BaseModel):
    disclosure_id: str
    metric_key: str
    group: dict[str, Any]
    revealed_event_hashes: list[str]
    challenge_subject: str


class OrchestratorStateChangedPayload(BaseModel):
    run_id: str
    workflow_name: str
    from_state: str | None = None
    to_state: str
    reason: str | None = None


class ToolInvocationLoggedPayload(BaseModel):
    run_id: str
    task_id: str
    connector: str
    action: str
    status: Literal["success", "failed"]
    attempt: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_retries: int = Field(ge=0)
    request_hash: str
    response_hash: str | None = None
    error: str | None = None
    governance: dict[str, Any] = Field(default_factory=dict)


class DemoScenarioInitializedPayload(BaseModel):
    scenario_id: str
    scenario_version: str
    seeded_at: str
    key_event_ids: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "ProcurementOrdered": ProcurementOrderedPayload,
    "GoodsReceived": GoodsReceivedPayload,
    "OrderPlaced": OrderPlacedPayload,
    "PaymentCaptured": PaymentCapturedPayload,
    "ShipmentDispatched": ShipmentDispatchedPayload,
    "RefundIssued": RefundIssuedPayload,
    "InventoryAdjusted": InventoryAdjustedPayload,
    "DisclosurePublished": DisclosurePublishedPayload,
    "SelectiveDisclosureRevealed": SelectiveDisclosureRevealedPayload,
    "OrchestratorStateChanged": OrchestratorStateChangedPayload,
    "ToolInvocationLogged": ToolInvocationLoggedPayload,
    "DemoScenarioInitialized": DemoScenarioInitializedPayload,
}


class LedgerEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: ActorModel
    policy_id: str = "policy_internal_v1"
    payload: dict[str, Any]
    tool_trace: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = "0" * 64
    signature: str = ""

    @field_validator("occurred_at")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in PAYLOAD_MODELS:
            raise ValueError(f"unsupported event_type: {value}")
        return value

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, Any], info):
        event_type = info.data.get("event_type")
        model = PAYLOAD_MODELS.get(event_type)
        if model is None:
            return value
        return model.model_validate(value).model_dump()


class EventCreateRequest(BaseModel):
    event_type: str
    actor: ActorModel
    policy_id: str = "policy_internal_v1"
    payload: dict[str, Any]
    tool_trace: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    def to_ledger_event(self, prev_hash: str) -> LedgerEvent:
        return LedgerEvent(
            event_type=self.event_type,
            actor=self.actor,
            policy_id=self.policy_id,
            payload=self.payload,
            tool_trace=self.tool_trace,
            occurred_at=self.occurred_at or datetime.now(timezone.utc),
            prev_hash=prev_hash,
        )
