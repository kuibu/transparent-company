from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.demo import get_default_scenario_story, seed_default_scenario
from app.demo.default_scenario import _build_superset_template
from app.persistence.pg import get_session

router = APIRouter(tags=["demo"])


@router.post("/demo/seed")
def demo_seed(
    detail_level: Literal["summary", "full"] = "full",
    session: Session = Depends(get_session),
):
    return seed_default_scenario(session, detail_level=detail_level)


@router.get("/demo/default/story")
def demo_default_story(
    detail_level: Literal["summary", "full"] = "summary",
    session: Session = Depends(get_session),
):
    return get_default_scenario_story(session, detail_level=detail_level)


@router.get("/demo/default/assets")
def demo_default_assets(
    detail_level: Literal["summary", "full"] = "summary",
    session: Session = Depends(get_session),
):
    story = get_default_scenario_story(session, detail_level=detail_level)
    return {
        "scenario_id": story.get("scenario_id"),
        "data_exports": story.get("data_exports", {}),
        "soul_manifest": story.get("soul_manifest", []),
        "public_detail_level": story.get("public_detail_level", detail_level),
    }


@router.get("/demo/default/superset-template")
def demo_default_superset_template():
    return _build_superset_template()
