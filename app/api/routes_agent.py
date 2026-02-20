from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.chat_service import AgentChatService
from app.agent.connectors import get_connector, list_connectors_with_permissions
from app.agent.memory_backend import build_memory_backend
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


class AgentProfileUpsertRequest(BaseModel):
    agent_id: str
    mission: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    display_name: str | None = None
    metadata: dict = Field(default_factory=dict)


class ConversationCreateRequest(BaseModel):
    agent_id: str
    counterpart_type: str = Field(pattern="^(human|agent|auditor)$")
    counterpart_id: str
    conversation_id: str | None = None
    seed_system_prompt: bool = True


class ConversationMessageRequest(BaseModel):
    sender_type: str = Field(pattern="^(agent|human|auditor|system)$")
    sender_id: str
    content: str = Field(min_length=1)
    is_decision: bool = False
    metadata: dict = Field(default_factory=dict)


class AgentChatRequest(BaseModel):
    speaker_agent_id: str
    user_sender_type: str = Field(default="human", pattern="^(human|agent|auditor)$")
    user_sender_id: str = "human-001"
    user_message: str = Field(min_length=1)
    record_reply_as_decision: bool = True
    memory_limit: int = Field(default=3, ge=1, le=20)


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


@router.get("/agent/memory/backend/health")
def memory_backend_health():
    backend = build_memory_backend()
    health = backend.health()
    return {
        "backend": health.backend,
        "healthy": health.healthy,
        "detail": health.detail,
        "raw": health.raw,
    }


@router.post("/agent/memory/profiles")
def upsert_agent_profile(
    request: AgentProfileUpsertRequest,
    session: Session = Depends(get_session),
):
    svc = AgentChatService(session)
    row = svc.upsert_profile(
        agent_id=request.agent_id,
        mission=request.mission,
        system_prompt=request.system_prompt,
        display_name=request.display_name,
        metadata=request.metadata,
    )
    return {
        "agent_id": row.agent_id,
        "display_name": row.display_name,
        "mission": row.mission,
        "system_prompt": row.system_prompt,
        "metadata": row.metadata_json,
        "updated_at": row.updated_at.isoformat().replace("+00:00", "Z"),
    }


@router.get("/agent/memory/profiles/{agent_id}")
def get_agent_profile(agent_id: str, session: Session = Depends(get_session)):
    svc = AgentChatService(session)
    row = svc.get_profile(agent_id)
    if row is None:
        raise HTTPException(status_code=404, detail="agent profile not found")
    return {
        "agent_id": row.agent_id,
        "display_name": row.display_name,
        "mission": row.mission,
        "system_prompt": row.system_prompt,
        "metadata": row.metadata_json,
        "updated_at": row.updated_at.isoformat().replace("+00:00", "Z"),
    }


@router.post("/agent/memory/conversations")
def create_agent_conversation(
    request: ConversationCreateRequest,
    session: Session = Depends(get_session),
):
    svc = AgentChatService(session)
    try:
        row = svc.create_conversation(
            agent_id=request.agent_id,
            counterpart_type=request.counterpart_type,
            counterpart_id=request.counterpart_id,
            conversation_id=request.conversation_id,
            seed_system_prompt=request.seed_system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "conversation_id": row.conversation_id,
        "agent_id": row.agent_id,
        "counterpart_type": row.counterpart_type,
        "counterpart_id": row.counterpart_id,
        "memory_backend": row.memory_backend,
        "memory_session_id": row.memory_session_id,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
    }


@router.get("/agent/memory/conversations/{conversation_id}")
def get_agent_conversation(conversation_id: str, session: Session = Depends(get_session)):
    svc = AgentChatService(session)
    try:
        return svc.get_conversation(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agent/memory/conversations/{conversation_id}/messages")
def add_agent_conversation_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    session: Session = Depends(get_session),
):
    svc = AgentChatService(session)
    try:
        row = svc.add_message(
            conversation_id=conversation_id,
            sender_type=request.sender_type,
            sender_id=request.sender_id,
            content=request.content,
            is_decision=request.is_decision,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "message_id": row.id,
        "conversation_id": row.conversation_id,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "role": row.role,
        "is_decision": row.is_decision,
        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
    }


@router.post("/agent/memory/conversations/{conversation_id}/chat")
def agent_chat_with_memory(
    conversation_id: str,
    request: AgentChatRequest,
    session: Session = Depends(get_session),
):
    svc = AgentChatService(session)
    try:
        reply = svc.agent_reply(
            conversation_id=conversation_id,
            speaker_agent_id=request.speaker_agent_id,
            user_sender_type=request.user_sender_type,
            user_sender_id=request.user_sender_id,
            user_message=request.user_message,
            record_reply_as_decision=request.record_reply_as_decision,
            memory_limit=request.memory_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "conversation_id": reply.conversation_id,
        "user_message_id": reply.user_message_id,
        "assistant_message_id": reply.assistant_message_id,
        "assistant_text": reply.assistant_text,
        "mission": reply.mission,
        "memory_hits": reply.memory_hits,
        "backend": {
            "requested_backend": reply.backend_status.requested_backend,
            "active_backend": reply.backend_status.active_backend,
            "healthy": reply.backend_status.healthy,
            "detail": reply.backend_status.detail,
        },
    }


@router.post("/agent/memory/conversations/{conversation_id}/commit")
def commit_agent_memory(conversation_id: str, session: Session = Depends(get_session)):
    svc = AgentChatService(session)
    try:
        return svc.commit_memory(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/agent/memory/conversations/{conversation_id}/memory/search")
def search_agent_memory(
    conversation_id: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    session: Session = Depends(get_session),
):
    svc = AgentChatService(session)
    try:
        hits, backend_status = svc.search_memory(conversation_id=conversation_id, query=q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "conversation_id": conversation_id,
        "query": q,
        "hits": [
            {
                "text": item.text,
                "score": item.score,
                "uri": item.uri,
                "metadata": item.metadata,
            }
            for item in hits
        ],
        "backend": {
            "requested_backend": backend_status.requested_backend,
            "active_backend": backend_status.active_backend,
            "healthy": backend_status.healthy,
            "detail": backend_status.detail,
        },
    }
