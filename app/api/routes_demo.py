from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.demo import get_default_scenario_story, seed_default_scenario
from app.persistence.pg import get_session

router = APIRouter(tags=["demo"])


@router.post("/demo/seed")
def demo_seed(session: Session = Depends(get_session)):
    return seed_default_scenario(session)


@router.get("/demo/default/story")
def demo_default_story(session: Session = Depends(get_session)):
    return get_default_scenario_story(session)
