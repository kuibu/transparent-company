from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.skills.executor import SkillExecutor
from app.agent.skills.registry import SkillRegistry
from app.agent.skills.router import SkillRouter
from app.core.security import Actor
from app.persistence.models import LedgerEventModel


def _write_skill(
    root: Path,
    *,
    name: str,
    entrypoint: str,
    triggers: list[str],
    permissions: list[str],
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    trigger_lines = "\n".join([f"  - {item}" for item in triggers])
    permission_lines = "\n".join([f"  - {item}" for item in permissions])
    skill_file.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"entrypoint: {entrypoint}",
                f"description: {name} test skill",
                "triggers:",
                trigger_lines,
                "permissions:",
                permission_lines,
                "---",
                f"# {name}",
                "SOP text.",
            ]
        ),
        encoding="utf-8",
    )


def test_skill_registry_and_parser(tmp_path: Path):
    _write_skill(
        tmp_path,
        name="procurement",
        entrypoint="procurement.run",
        triggers=["采购", "procurement"],
        permissions=["ledger_write", "inventory_write"],
    )
    _write_skill(
        tmp_path,
        name="disclosure",
        entrypoint="disclosure.run",
        triggers=["披露", "disclosure"],
        permissions=["disclosure_publish"],
    )

    registry = SkillRegistry.load(tmp_path)

    assert registry.names() == ["disclosure", "procurement"]
    procurement = registry.get("procurement")
    assert procurement is not None
    assert procurement.entrypoint == "procurement.run"
    assert procurement.triggers == ("采购", "procurement")
    assert procurement.permissions == ("ledger_write", "inventory_write")
    assert procurement.risk_level == "low"


def test_skill_router_explicit_trigger_and_high_risk_policy(tmp_path: Path):
    _write_skill(
        tmp_path,
        name="procurement",
        entrypoint="procurement.run",
        triggers=["采购", "进货"],
        permissions=["ledger_write"],
    )
    _write_skill(
        tmp_path,
        name="network_skill",
        entrypoint="network.run",
        triggers=["联网"],
        permissions=["network"],
    )

    registry = SkillRegistry.load(tmp_path)
    router = SkillRouter.from_config(registry, max_autoload_risk="high", approved_list_csv="")

    explicit = router.route("skill:procurement 今天进货")
    assert explicit is not None
    assert explicit.manifest.name == "procurement"
    assert explicit.reason == "explicit_skill_prefix"

    trigger = router.route("请帮我采购一批青菜")
    assert trigger is not None
    assert trigger.manifest.name == "procurement"
    assert trigger.reason == "trigger_keyword_match"

    assert router.route("这是一条无关请求") is None

    with pytest.raises(PermissionError):
        router.route("skill:network_skill 请联网执行")


def test_skill_executor_writes_started_and_finished_events(session, tmp_path: Path):
    _write_skill(
        tmp_path,
        name="procurement",
        entrypoint="procurement.run",
        triggers=["采购"],
        permissions=["ledger_write", "inventory_write"],
    )

    registry = SkillRegistry.load(tmp_path)
    router = SkillRouter.from_config(registry, max_autoload_risk="high", approved_list_csv="")
    executor = SkillExecutor(
        session=session,
        actor=Actor(type="agent", id="agent-001"),
        registry=registry,
        router=router,
    )

    result = executor.run("skill:procurement 今天进100斤青菜 供货商A 单价3.2")

    rows = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type.in_(["SkillRunStarted", "SkillRunFinished"]))
            .order_by(LedgerEventModel.seq_id.asc())
        ).all()
    )
    by_type = {row.event_type: row for row in rows if row.payload.get("run_id") == result.run_id}

    started = by_type.get("SkillRunStarted")
    finished = by_type.get("SkillRunFinished")
    assert started is not None
    assert finished is not None

    assert started.payload["skill_name"] == "procurement"
    assert started.payload["inputs_hash"] == result.inputs_hash
    assert finished.payload["outputs_hash"] == result.outputs_hash
    assert result.output["procurement_id"].startswith("SKILL-PO-")


def test_skill_executor_writes_failed_event_on_missing_entrypoint(session, tmp_path: Path):
    _write_skill(
        tmp_path,
        name="broken",
        entrypoint="missing.entrypoint",
        triggers=["broken"],
        permissions=["ledger_write"],
    )

    registry = SkillRegistry.load(tmp_path)
    router = SkillRouter.from_config(registry, max_autoload_risk="high", approved_list_csv="")
    executor = SkillExecutor(
        session=session,
        actor=Actor(type="agent", id="agent-001"),
        registry=registry,
        router=router,
    )

    with pytest.raises(RuntimeError):
        executor.run("skill:broken run now")

    rows = list(
        session.scalars(
            select(LedgerEventModel)
            .where(LedgerEventModel.event_type.in_(["SkillRunStarted", "SkillRunFailed"]))
            .order_by(LedgerEventModel.seq_id.desc())
            .limit(20)
        ).all()
    )

    has_started = any(row.payload.get("skill_name") == "broken" and row.event_type == "SkillRunStarted" for row in rows)
    has_failed = any(row.payload.get("skill_name") == "broken" and row.event_type == "SkillRunFailed" for row in rows)
    assert has_started
    assert has_failed
