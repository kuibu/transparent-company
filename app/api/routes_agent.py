from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.connectors import get_connector, list_connectors_with_permissions
from app.agent.orchestrator import AgentOrchestrator, OrchestratorRunRequest
from app.core.key_management import expected_signer_role, public_key_manifest
from app.core.security import Actor, get_actor
from app.governance import PolicyEnforcementError, get_governance_engine
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.pg import get_session

router = APIRouter(tags=["agent", "governance"])


class ToolInvokeRequest(BaseModel):
    payload: dict = Field(default_factory=dict)
    approvals: list[str] = Field(default_factory=list)


@router.get("/governance/policy")
def get_governance_policy():
    return get_governance_engine().policy_manifest()


@router.get("/keys/public")
def get_public_keys():
    return {"keys": public_key_manifest()}


@router.get("/agent/tools")
def list_tools():
    return {
        "tools": list_connectors_with_permissions(),
        "governance_policy_hash": get_governance_engine().policy_manifest()["policy_hash"],
    }


@router.post("/agent/tools/{connector_name}/{action}")
def invoke_tool(
    connector_name: str,
    action: str,
    request: ToolInvokeRequest,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    try:
        connector = get_connector(connector_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    signer_role = expected_signer_role(actor.type)
    signer = load_role_key(signer_role)

    try:
        result = connector.invoke(
            action=action,
            payload=request.payload,
            actor_type=actor.type,
            signer_role=signer_role,
            approvals=request.approvals,
        )
    except PolicyEnforcementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = f"manual-{uuid4().hex[:12]}"
    task_id = f"{connector_name}-{action}"
    event = EventCreateRequest(
        event_type="ToolInvocationLogged",
        actor={"type": actor.type, "id": actor.id},
        policy_id="policy_internal_v1",
        payload={
            "run_id": run_id,
            "task_id": task_id,
            "connector": connector_name,
            "action": action,
            "status": "success",
            "attempt": 1,
            "timeout_seconds": 30,
            "max_retries": 0,
            "request_hash": result.request_hash,
            "response_hash": result.response_hash,
            "error": None,
            "governance": result.governance,
        },
        tool_trace={
            "connector": connector_name,
            "action": action,
            "manual_call": True,
            "called_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )
    LedgerStore(session).append(event, signer=signer)

    return {
        "run_id": run_id,
        "task_id": task_id,
        "result": result.model_dump(),
    }


@router.post("/agent/orchestrator/run")
def run_orchestrator(
    request: OrchestratorRunRequest,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    try:
        return AgentOrchestrator(session).run(request, actor=actor)
    except PolicyEnforcementError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
