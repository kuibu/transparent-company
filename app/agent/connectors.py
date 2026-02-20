from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field

from app.governance import GovernancePolicyEngine, PolicyEnforcementError, get_governance_engine
from app.ledger.canonical import canonical_json


class ConnectorPermission(BaseModel):
    connector: str
    action: str
    risk_tier: str
    requires_human_signature: bool = False
    description: str


class ConnectorResult(BaseModel):
    connector: str
    action: str
    status: str
    request_hash: str
    response_hash: str
    receipt_hash: str
    response: dict[str, Any]
    governance: dict[str, Any]


class BaseToolConnector:
    connector_name: str = "base"
    permissions: dict[str, ConnectorPermission] = {}

    def permission_list(self) -> list[dict[str, Any]]:
        return [item.model_dump() for item in self.permissions.values()]

    def invoke(
        self,
        action: str,
        payload: dict[str, Any],
        actor_type: str,
        signer_role: str,
        approvals: list[str] | None = None,
        governance: GovernancePolicyEngine | None = None,
    ) -> ConnectorResult:
        permission = self.permissions.get(action)
        if permission is None:
            raise ValueError(f"unsupported action={action} for connector={self.connector_name}")

        engine = governance or get_governance_engine()
        decision = engine.evaluate(
            action=f"tool:{self.connector_name}.{action}",
            actor_type=actor_type,
            signer_role=signer_role,
            payload=payload,
            tool_trace={"connector": self.connector_name, "action": action},
            approvals=approvals or [],
        )
        if not decision.allowed:
            raise PolicyEnforcementError(decision.reason)

        response = self._simulate(action=action, payload=payload)

        request_hash = sha256(canonical_json(payload)).hexdigest()
        response_hash = sha256(canonical_json(response)).hexdigest()
        receipt_hash = sha256(
            canonical_json(
                {
                    "connector": self.connector_name,
                    "action": action,
                    "request_hash": request_hash,
                    "response_hash": response_hash,
                    "at": datetime.now(timezone.utc),
                }
            )
        ).hexdigest()

        return ConnectorResult(
            connector=self.connector_name,
            action=action,
            status="ok",
            request_hash=request_hash,
            response_hash=response_hash,
            receipt_hash=receipt_hash,
            response=response,
            governance=decision.to_audit_dict(),
        )

    def _simulate(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "connector": self.connector_name,
            "action": action,
            "accepted": True,
            "echo": payload,
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }


class PaymentConnector(BaseToolConnector):
    connector_name = "payment"
    permissions = {
        "capture": ConnectorPermission(
            connector="payment",
            action="capture",
            risk_tier="medium",
            description="Capture payment for a customer order",
        ),
        "refund": ConnectorPermission(
            connector="payment",
            action="refund",
            risk_tier="medium",
            description="Issue refund to customer",
        ),
        "bank_transfer": ConnectorPermission(
            connector="payment",
            action="bank_transfer",
            risk_tier="high",
            requires_human_signature=True,
            description="High-risk bank transfer",
        ),
    }


class LogisticsConnector(BaseToolConnector):
    connector_name = "logistics"
    permissions = {
        "dispatch": ConnectorPermission(
            connector="logistics",
            action="dispatch",
            risk_tier="medium",
            description="Create shipment dispatch",
        ),
        "track": ConnectorPermission(
            connector="logistics",
            action="track",
            risk_tier="low",
            description="Track shipment status",
        ),
    }


class EcommerceConnector(BaseToolConnector):
    connector_name = "ecommerce"
    permissions = {
        "accept_order": ConnectorPermission(
            connector="ecommerce",
            action="accept_order",
            risk_tier="low",
            description="Accept order from ecommerce channel",
        ),
        "sync_inventory": ConnectorPermission(
            connector="ecommerce",
            action="sync_inventory",
            risk_tier="low",
            description="Sync inventory snapshot",
        ),
    }


class ESignatureConnector(BaseToolConnector):
    connector_name = "esign"
    permissions = {
        "sign_contract_final": ConnectorPermission(
            connector="esign",
            action="sign_contract_final",
            risk_tier="high",
            requires_human_signature=True,
            description="Final legal contract signature",
        ),
    }


class SupplierConnector(BaseToolConnector):
    connector_name = "supplier"
    permissions = {
        "place_order": ConnectorPermission(
            connector="supplier",
            action="place_order",
            risk_tier="medium",
            description="Send purchase order to supplier",
        ),
        "request_quote": ConnectorPermission(
            connector="supplier",
            action="request_quote",
            risk_tier="low",
            description="Request quotation",
        ),
    }


CONNECTORS: dict[str, BaseToolConnector] = {
    "payment": PaymentConnector(),
    "logistics": LogisticsConnector(),
    "ecommerce": EcommerceConnector(),
    "esign": ESignatureConnector(),
    "supplier": SupplierConnector(),
}


def get_connector(name: str) -> BaseToolConnector:
    connector = CONNECTORS.get(name)
    if connector is None:
        raise KeyError(f"unknown connector: {name}")
    return connector


def list_connectors_with_permissions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in sorted(CONNECTORS.keys()):
        connector = CONNECTORS[name]
        out.append(
            {
                "connector": name,
                "permissions": connector.permission_list(),
            }
        )
    return out
