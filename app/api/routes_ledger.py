from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ledger.anchoring import AnchoringService
from app.persistence.pg import get_session

router = APIRouter(tags=["ledger"])


@router.get("/anchor/disclosure/{disclosure_id}")
def get_disclosure_anchor(disclosure_id: str, session: Session = Depends(get_session)):
    anchor = AnchoringService(session).get_disclosure_anchor(disclosure_id)
    if not anchor:
        raise HTTPException(status_code=404, detail="anchor not found")
    return anchor
