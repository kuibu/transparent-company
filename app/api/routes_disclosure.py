from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.utils import now_utc, parse_period
from app.core.security import Actor, get_actor
from app.disclosure.commitment import normalize_group_param, proof_lookup_key
from app.disclosure.policies import get_policy, list_policies
from app.disclosure.publisher import publish_disclosure_run
from app.disclosure.selective import SelectiveDisclosureService
from app.persistence.models import DisclosureRunModel
from app.persistence.pg import get_session

router = APIRouter(tags=["disclosure"])


class PublishDisclosureRequest(BaseModel):
    policy_id: str
    period: str = Field(description="ISO format start/end")
    group_by: list[str] | None = None


class SelectiveRevealRequest(BaseModel):
    token: str
    metric_key: str
    group: dict = Field(default_factory=dict)


@router.get("/disclosure/policies")
def get_disclosure_policies():
    items = []
    for policy in list_policies():
        items.append({**policy.model_dump(), "policy_hash": policy.policy_hash()})
    return {"policies": items}


@router.post("/disclosure/publish")
def publish_disclosure(
    request: PublishDisclosureRequest,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    try:
        policy = get_policy(request.policy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        period_start, period_end = parse_period(request.period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    delay_cutoff = now_utc() - timedelta(days=policy.delay_days)
    if period_end > delay_cutoff:
        raise HTTPException(
            status_code=400,
            detail=f"period too recent for policy delay_days={policy.delay_days}",
        )

    result = publish_disclosure_run(
        session=session,
        policy_id=policy.policy_id,
        period_start=period_start,
        period_end=period_end,
        group_by=request.group_by,
        actor=actor,
    )
    return result.payload


@router.get("/disclosure/{disclosure_id}")
def get_disclosure(disclosure_id: str, session: Session = Depends(get_session)):
    run = session.get(DisclosureRunModel, disclosure_id)
    if not run:
        raise HTTPException(status_code=404, detail="disclosure not found")
    return {
        "disclosure_id": run.disclosure_id,
        "policy_id": run.policy_id,
        "policy_hash": run.policy_hash,
        "period": {
            "start": run.period_start.isoformat().replace("+00:00", "Z"),
            "end": run.period_end.isoformat().replace("+00:00", "Z"),
        },
        "root_summary": run.root_summary,
        "root_details": run.root_details,
        "statement": run.statement_json,
        "statement_signature": run.statement_signature,
        "anchor_ref": run.anchor_ref,
    }


@router.get("/disclosure/{disclosure_id}/proof")
def get_disclosure_proof(
    disclosure_id: str,
    metric_key: str = Query(...),
    group: str | None = Query(default=None, description="JSON object or k=v,k2=v2"),
    session: Session = Depends(get_session),
):
    run = session.get(DisclosureRunModel, disclosure_id)
    if not run:
        raise HTTPException(status_code=404, detail="disclosure not found")
    group_dict = normalize_group_param(group)
    key = proof_lookup_key(metric_key, group_dict)
    proof_item = run.proof_index.get(key)
    if not proof_item:
        raise HTTPException(status_code=404, detail="proof not found for metric/group")
    return {
        "disclosure_id": disclosure_id,
        "metric_key": metric_key,
        "group": group_dict,
        "root_summary": run.root_summary,
        "proof": proof_item,
    }


@router.get("/disclosure/{disclosure_id}/selective/request")
def selective_request(
    disclosure_id: str,
    subject: str = Query(default="auditor-demo"),
    session: Session = Depends(get_session),
):
    if session.get(DisclosureRunModel, disclosure_id) is None:
        raise HTTPException(status_code=404, detail="disclosure not found")
    return SelectiveDisclosureService(session).request_token(disclosure_id=disclosure_id, subject=subject)


@router.post("/disclosure/{disclosure_id}/selective/reveal")
def selective_reveal(
    disclosure_id: str,
    request: SelectiveRevealRequest,
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
):
    return SelectiveDisclosureService(session).reveal(
        disclosure_id=disclosure_id,
        token=request.token,
        metric_key=request.metric_key,
        group=request.group,
        actor=actor,
    )
