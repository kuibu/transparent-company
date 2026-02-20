from __future__ import annotations

from fastapi import FastAPI

from app.api.routes_demo import router as demo_router
from app.api.routes_disclosure import router as disclosure_router
from app.api.routes_ledger import router as ledger_router
from app.api.routes_reports import router as reports_router
from app.core.logging import configure_logging
from app.persistence.pg import init_db

configure_logging()
app = FastAPI(title="Transparent Company MVP+")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


app.include_router(demo_router)
app.include_router(disclosure_router)
app.include_router(ledger_router)
app.include_router(reports_router)
