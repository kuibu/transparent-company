from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ledger.canonical import sha256_hex


ActorType = Literal["agent", "human", "system", "auditor"]
SignerRole = Literal["agent", "human", "auditor"]
ConditionOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists"]


class PolicyEnforcementError(PermissionError):
    pass


class RuleCondition(BaseModel):
    field: str = Field(description="dot-path in evaluation context, e.g. payload.amount")
    op: ConditionOp
    value: Any | None = None


class GovernanceRule(BaseModel):
    rule_id: str
    action: str
    description: str
    conditions: list[RuleCondition] = Field(default_factory=list)
    required_actor_types: list[ActorType] = Field(default_factory=list)
    required_signer: Literal["agent", "human", "auditor", "any"] = "any"
    approval_chain: list[SignerRole] = Field(default_factory=list)


class GovernancePolicy(BaseModel):
    policy_id: str
    version: str
    rules: list[GovernanceRule]

    def policy_hash(self) -> str:
        return sha256_hex(self.model_dump())


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    policy_id: str
    policy_version: str
    policy_hash: str
    action: str
    matched_rule_id: str | None
    reason: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "action": self.action,
            "matched_rule_id": self.matched_rule_id,
            "reason": self.reason,
        }


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _matches(condition: RuleCondition, context: dict[str, Any]) -> bool:
    left = _get_path(context, condition.field)
    right = condition.value

    if condition.op == "exists":
        return left is not None
    if condition.op == "eq":
        return left == right
    if condition.op == "ne":
        return left != right
    if condition.op == "gt":
        return left is not None and left > right
    if condition.op == "gte":
        return left is not None and left >= right
    if condition.op == "lt":
        return left is not None and left < right
    if condition.op == "lte":
        return left is not None and left <= right
    if condition.op == "in":
        return left in (right or [])
    if condition.op == "contains":
        return right in (left or [])
    return False


def _derive_values(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    derived: dict[str, Any] = {}

    if action == "event:ProcurementOrdered":
        total = 0
        for item in payload.get("items", []):
            total += int(item.get("qty", 0)) * int(item.get("unit_cost", 0))
        derived["procurement_total_cents"] = total

    if "amount" in payload:
        try:
            derived["amount_cents"] = int(payload["amount"])
        except Exception:
            pass

    return derived


DEFAULT_GOVERNANCE_POLICY = GovernancePolicy(
    policy_id="governance_policy_v1",
    version="1.0.0",
    rules=[
        GovernanceRule(
            rule_id="procurement_gt_5000_human",
            action="event:ProcurementOrdered",
            description="Single procurement above $5000 requires human signature",
            conditions=[RuleCondition(field="derived.procurement_total_cents", op="gt", value=500_000)],
            required_actor_types=["human"],
            required_signer="human",
            approval_chain=["human"],
        ),
        GovernanceRule(
            rule_id="bank_transfer_human_only",
            action="tool:payment.bank_transfer",
            description="High-risk bank transfer requires human signature",
            required_actor_types=["human"],
            required_signer="human",
            approval_chain=["human"],
        ),
        GovernanceRule(
            rule_id="tax_submit_human_only",
            action="tool:tax.submit_final",
            description="Final tax submission requires human signature",
            required_actor_types=["human"],
            required_signer="human",
            approval_chain=["human"],
        ),
        GovernanceRule(
            rule_id="major_contract_human_only",
            action="tool:esign.sign_contract_final",
            description="Major contract final signature requires human",
            required_actor_types=["human"],
            required_signer="human",
            approval_chain=["human"],
        ),
    ],
)


class GovernancePolicyEngine:
    def __init__(self, policy: GovernancePolicy | None = None):
        self.policy = policy or DEFAULT_GOVERNANCE_POLICY
        self._policy_hash = self.policy.policy_hash()

    def policy_manifest(self) -> dict[str, Any]:
        data = self.policy.model_dump()
        data["policy_hash"] = self._policy_hash
        return data

    def evaluate(
        self,
        action: str,
        actor_type: ActorType,
        signer_role: SignerRole,
        payload: dict[str, Any] | None = None,
        tool_trace: dict[str, Any] | None = None,
        approvals: list[str] | None = None,
    ) -> GovernanceDecision:
        payload = payload or {}
        tool_trace = tool_trace or {}
        approvals = approvals or []
        effective_approvals = {str(x) for x in approvals}
        effective_approvals.add(signer_role)
        if actor_type in {"agent", "human", "auditor"}:
            effective_approvals.add(actor_type)

        context = {
            "action": action,
            "actor_type": actor_type,
            "signer_role": signer_role,
            "payload": payload,
            "tool_trace": tool_trace,
            "approvals": sorted(effective_approvals),
            "derived": _derive_values(action, payload),
        }

        for rule in self.policy.rules:
            if rule.action != action:
                continue
            if rule.conditions and not all(_matches(cond, context) for cond in rule.conditions):
                continue

            if rule.required_actor_types and actor_type not in rule.required_actor_types:
                return GovernanceDecision(
                    allowed=False,
                    policy_id=self.policy.policy_id,
                    policy_version=self.policy.version,
                    policy_hash=self._policy_hash,
                    action=action,
                    matched_rule_id=rule.rule_id,
                    reason=f"actor_type={actor_type} not allowed by rule={rule.rule_id}",
                )

            if rule.required_signer != "any" and signer_role != rule.required_signer:
                return GovernanceDecision(
                    allowed=False,
                    policy_id=self.policy.policy_id,
                    policy_version=self.policy.version,
                    policy_hash=self._policy_hash,
                    action=action,
                    matched_rule_id=rule.rule_id,
                    reason=f"signer_role={signer_role} must be {rule.required_signer}",
                )

            missing_approvals = [x for x in rule.approval_chain if x not in effective_approvals]
            if missing_approvals:
                return GovernanceDecision(
                    allowed=False,
                    policy_id=self.policy.policy_id,
                    policy_version=self.policy.version,
                    policy_hash=self._policy_hash,
                    action=action,
                    matched_rule_id=rule.rule_id,
                    reason=f"missing approvals: {missing_approvals}",
                )

            return GovernanceDecision(
                allowed=True,
                policy_id=self.policy.policy_id,
                policy_version=self.policy.version,
                policy_hash=self._policy_hash,
                action=action,
                matched_rule_id=rule.rule_id,
                reason="allowed by matched rule",
            )

        return GovernanceDecision(
            allowed=True,
            policy_id=self.policy.policy_id,
            policy_version=self.policy.version,
            policy_hash=self._policy_hash,
            action=action,
            matched_rule_id=None,
            reason="allowed by default (no matched rule)",
        )


def get_governance_engine() -> GovernancePolicyEngine:
    return GovernancePolicyEngine()
