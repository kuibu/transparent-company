from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.skills.models import SkillManifest
from app.core.security import Actor
from app.disclosure.policies import get_policy
from app.disclosure.publisher import publish_disclosure_run
from app.domain.inventory.commands import order_procurement, receive_goods
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.core.key_management import expected_signer_role


@dataclass(frozen=True)
class SkillExecutionContext:
    session: Session
    actor: Actor
    manifest: SkillManifest
    run_id: str


SkillEntrypoint = Callable[[SkillExecutionContext, str], dict[str, Any]]
SKILL_ENTRYPOINTS: dict[str, SkillEntrypoint] = {}


def register_entrypoint(name: str) -> Callable[[SkillEntrypoint], SkillEntrypoint]:
    def _wrap(fn: SkillEntrypoint) -> SkillEntrypoint:
        if name in SKILL_ENTRYPOINTS:
            raise ValueError(f"duplicate skill entrypoint registration: {name}")
        SKILL_ENTRYPOINTS[name] = fn
        return fn

    return _wrap


def get_entrypoint(name: str) -> SkillEntrypoint | None:
    return SKILL_ENTRYPOINTS.get(name)


def _parse_decimal_yuan(raw: str, default: Decimal) -> Decimal:
    text = raw.strip()
    if not text:
        return default
    try:
        return Decimal(text)
    except Exception:
        return default


def _yuan_to_cents(value: Decimal) -> int:
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _slug_supplier(name: str) -> str:
    raw = name.strip().lower()
    if not raw:
        return "supplier-auto"
    safe = re.sub(r"[^a-z0-9_-]", "-", raw)
    safe = re.sub(r"-+", "-", safe).strip("-")
    if not safe:
        return f"supplier-{uuid4().hex[:8]}"
    return f"supplier-{safe}"


def _start_of_day(dt: datetime) -> datetime:
    return datetime.combine(dt.date(), time.min, tzinfo=timezone.utc)


def _previous_month_window(now: datetime) -> tuple[datetime, datetime]:
    first_day_this_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    last_day_prev_month = first_day_this_month - timedelta(days=1)
    first_day_prev_month = datetime(last_day_prev_month.year, last_day_prev_month.month, 1, tzinfo=timezone.utc)
    return first_day_prev_month, first_day_this_month


def _previous_week_window(now: datetime) -> tuple[datetime, datetime]:
    today_start = _start_of_day(now)
    this_week_start = today_start - timedelta(days=today_start.weekday())
    prev_week_start = this_week_start - timedelta(days=7)
    return prev_week_start, this_week_start


def _day_window(now: datetime) -> tuple[datetime, datetime]:
    today_start = _start_of_day(now)
    return today_start - timedelta(days=1), today_start


def _adjust_for_delay(policy_id: str, period_start: datetime, period_end: datetime) -> tuple[datetime, datetime]:
    policy = get_policy(policy_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=policy.delay_days)
    window = period_end - period_start

    if window.total_seconds() <= 0:
        raise ValueError("invalid period window")

    attempts = 0
    while period_end > cutoff and attempts < 30:
        period_start -= window
        period_end -= window
        attempts += 1
    return period_start, period_end


@register_entrypoint("procurement.run")
def procurement_run(ctx: SkillExecutionContext, query: str) -> dict[str, Any]:
    text = query.strip()

    qty_match = re.search(r"(\d+)\s*(?:斤|kg|KG|公斤|件)?", text)
    qty = int(qty_match.group(1)) if qty_match else 100

    sku_match = re.search(r"进\s*\d+\s*(?:斤|kg|KG|公斤|件)?\s*([\u4e00-\u9fffA-Za-z0-9_-]+)", text)
    sku = sku_match.group(1) if sku_match else "vegetable"

    supplier_match = re.search(r"(?:供货商|supplier)\s*([\u4e00-\u9fffA-Za-z0-9_-]+)", text, flags=re.IGNORECASE)
    supplier_name = supplier_match.group(1) if supplier_match else "auto"
    supplier_id = _slug_supplier(supplier_name)

    price_match = re.search(r"(?:单价|unit_price|unitprice)\s*[=:]?\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    unit_price_yuan = _parse_decimal_yuan(price_match.group(1), Decimal("3.2")) if price_match else Decimal("3.2")
    unit_cost_cents = _yuan_to_cents(unit_price_yuan)

    now = datetime.now(timezone.utc)
    procurement_id = f"SKILL-PO-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
    batch_id = f"SKILL-BATCH-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
    expected_date = (now + timedelta(days=1)).date().isoformat()
    expiry_date = (now.date() + timedelta(days=7)).isoformat()

    signer = load_role_key(expected_signer_role(ctx.actor.type))
    ledger = LedgerStore(ctx.session)

    procurement_req = order_procurement(
        actor=ctx.actor,
        procurement_id=procurement_id,
        supplier_id=supplier_id,
        items=[{"sku": sku, "qty": qty, "unit_cost": unit_cost_cents}],
        expected_date=expected_date,
    )
    procurement_req.tool_trace = {
        "skill_run_id": ctx.run_id,
        "skill_name": ctx.manifest.name,
        "skill_entrypoint": ctx.manifest.entrypoint,
        "source": "skill_executor",
    }
    procurement_row = ledger.append(procurement_req, signer=signer)

    receive_req = receive_goods(
        actor=ctx.actor,
        procurement_id=procurement_id,
        batch_id=batch_id,
        items=[{"sku": sku, "qty": qty, "expiry_date": expiry_date, "unit_cost": unit_cost_cents}],
        qc_passed=True,
    )
    receive_req.tool_trace = {
        "skill_run_id": ctx.run_id,
        "skill_name": ctx.manifest.name,
        "skill_entrypoint": ctx.manifest.entrypoint,
        "source": "skill_executor",
    }
    receive_row = ledger.append(receive_req, signer=signer)

    return {
        "procurement_id": procurement_id,
        "batch_id": batch_id,
        "supplier_id": supplier_id,
        "sku": sku,
        "qty": qty,
        "unit_cost_cents": unit_cost_cents,
        "total_cost_cents": qty * unit_cost_cents,
        "ordered_event_id": procurement_row.event_id,
        "received_event_id": receive_row.event_id,
    }


@register_entrypoint("disclosure.run")
def disclosure_run(ctx: SkillExecutionContext, query: str) -> dict[str, Any]:
    text = query.strip().lower()

    if "auditor" in text or "审计" in query:
        policy_id = "policy_auditor_v1"
    elif "investor" in text or "投资" in query:
        policy_id = "policy_investor_v1"
    elif "root_only" in text:
        policy_id = "policy_public_root_only_v1"
    else:
        policy_id = "policy_public_v1"

    granularity_match = re.search(r"粒度\s*=\s*(日|周|月|day|week|month)", query, flags=re.IGNORECASE)
    granularity = granularity_match.group(1).lower() if granularity_match else "day"

    now = datetime.now(timezone.utc)
    if granularity in {"周", "week"}:
        period_start, period_end = _previous_week_window(now)
        group_by = ["channel", "category"]
    elif granularity in {"月", "month"}:
        period_start, period_end = _previous_month_window(now)
        group_by = ["region", "category"]
    else:
        period_start, period_end = _day_window(now)
        group_by = ["channel"]

    period_start, period_end = _adjust_for_delay(policy_id, period_start, period_end)

    publish_result = publish_disclosure_run(
        session=ctx.session,
        policy_id=policy_id,
        period_start=period_start,
        period_end=period_end,
        group_by=group_by,
        actor=ctx.actor,
    )
    payload = publish_result.payload

    return {
        "disclosure_id": publish_result.disclosure_id,
        "policy_id": payload["policy_id"],
        "period": payload["period"],
        "root_summary": payload["root_summary"],
        "root_details": payload.get("root_details"),
        "metrics": payload.get("metrics", {}),
    }
