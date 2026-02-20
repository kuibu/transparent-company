from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes_agent import router as agent_router
from app.api.routes_demo import router as demo_router
from app.api.routes_disclosure import router as disclosure_router
from app.api.routes_ledger import router as ledger_router
from app.api.routes_reports import router as reports_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.demo import seed_default_scenario
from app.governance import PolicyEnforcementError
from app.persistence.pg import init_db, session_scope

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="Transparent Company MVP+")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if settings.bootstrap_demo_on_startup:
        with session_scope() as session:
            result = seed_default_scenario(session)
        logger.info(
            "default demo scenario ready: scenario_id=%s seeded_now=%s",
            result.get("scenario_id"),
            result.get("seeded_now"),
        )


@app.exception_handler(PolicyEnforcementError)
async def policy_enforcement_handler(_: Request, exc: PolicyEnforcementError):
    return JSONResponse(
        status_code=403,
        content={
            "detail": str(exc),
            "error": "policy_enforcement",
        },
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


app.include_router(demo_router)
app.include_router(disclosure_router)
app.include_router(ledger_router)
app.include_router(reports_router)
app.include_router(agent_router)
