from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.skills.entrypoints import SkillExecutionContext, get_entrypoint
from app.agent.skills.models import SkillExecutionResult, SkillRouteResult
from app.agent.skills.registry import SkillRegistry
from app.agent.skills.router import SkillRouter
from app.core.config import get_settings
from app.core.key_management import expected_signer_role
from app.core.security import Actor
from app.ledger.canonical import sha256_hex
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore


class SkillExecutor:
    def __init__(
        self,
        session: Session,
        actor: Actor,
        registry: SkillRegistry,
        router: SkillRouter,
    ):
        self.session = session
        self.actor = actor
        self.registry = registry
        self.router = router
        self.ledger = LedgerStore(session)
        self.signer = load_role_key(expected_signer_role(actor.type))

    @classmethod
    def from_settings(
        cls,
        session: Session,
        actor: Actor,
        skills_root: str | Path | None = None,
    ) -> "SkillExecutor":
        settings = get_settings()
        root = Path(skills_root) if skills_root is not None else Path(settings.skills_root)
        registry = SkillRegistry.load(root)
        router = SkillRouter.from_config(
            registry=registry,
            max_autoload_risk=settings.skills_max_autoload_risk,
            approved_list_csv=settings.skills_approved_list,
        )
        return cls(session=session, actor=actor, registry=registry, router=router)

    def _append_audit(
        self,
        *,
        event_type: str,
        payload: dict,
        run_id: str,
        route: SkillRouteResult,
        trace_extra: dict | None = None,
    ) -> None:
        tool_trace = {
            "skill_run_id": run_id,
            "skill_name": route.manifest.name,
            "skill_entrypoint": route.manifest.entrypoint,
            "source": "skill_executor",
            "route_reason": route.reason,
        }
        if trace_extra:
            tool_trace.update(trace_extra)

        req = EventCreateRequest(
            event_type=event_type,
            actor={"type": self.actor.type, "id": self.actor.id},
            policy_id="policy_internal_v1",
            payload=payload,
            tool_trace=tool_trace,
        )
        self.ledger.append(req, signer=self.signer)

    def run(self, query: str) -> SkillExecutionResult:
        route = self.router.route(query)
        if route is None:
            raise ValueError("no skill matched; use 'skill:<name>' or a known trigger")

        run_id = f"skillrun-{uuid4().hex[:20]}"
        inputs = {
            "query": route.rewritten_query,
            "raw_query": query,
            "skill_name": route.manifest.name,
            "entrypoint": route.manifest.entrypoint,
        }
        inputs_hash = sha256_hex(inputs)
        sop_hash = sha256_hex({"sop": route.manifest.sop_markdown})

        self._append_audit(
            event_type="SkillRunStarted",
            payload={
                "run_id": run_id,
                "skill_name": route.manifest.name,
                "entrypoint": route.manifest.entrypoint,
                "actor_id": self.actor.id,
                "inputs_hash": inputs_hash,
                "outputs_hash": "",
                "permissions": list(route.manifest.permissions),
                "sop_hash": sop_hash,
                "receipt_hash": None,
            },
            run_id=run_id,
            route=route,
        )

        try:
            entrypoint = get_entrypoint(route.manifest.entrypoint)
            if entrypoint is None:
                raise RuntimeError(
                    f"entrypoint not registered for skill '{route.manifest.name}': {route.manifest.entrypoint}"
                )

            ctx = SkillExecutionContext(
                session=self.session,
                actor=self.actor,
                manifest=route.manifest,
                run_id=run_id,
            )
            output = entrypoint(ctx, route.rewritten_query)
            if not isinstance(output, dict):
                raise TypeError("skill entrypoint must return dict")

            outputs_hash = sha256_hex(output)
            receipt_hash = output.get("receipt_hash") if isinstance(output.get("receipt_hash"), str) else None

            self._append_audit(
                event_type="SkillRunFinished",
                payload={
                    "run_id": run_id,
                    "skill_name": route.manifest.name,
                    "entrypoint": route.manifest.entrypoint,
                    "actor_id": self.actor.id,
                    "inputs_hash": inputs_hash,
                    "outputs_hash": outputs_hash,
                    "receipt_hash": receipt_hash,
                },
                run_id=run_id,
                route=route,
                trace_extra={"outputs_hash": outputs_hash},
            )

            return SkillExecutionResult(
                run_id=run_id,
                skill_name=route.manifest.name,
                entrypoint=route.manifest.entrypoint,
                inputs_hash=inputs_hash,
                outputs_hash=outputs_hash,
                output=output,
            )
        except Exception as exc:
            self._append_audit(
                event_type="SkillRunFailed",
                payload={
                    "run_id": run_id,
                    "skill_name": route.manifest.name,
                    "entrypoint": route.manifest.entrypoint,
                    "actor_id": self.actor.id,
                    "inputs_hash": inputs_hash,
                    "outputs_hash": "",
                    "error": str(exc),
                },
                run_id=run_id,
                route=route,
                trace_extra={"error": str(exc)},
            )
            raise
