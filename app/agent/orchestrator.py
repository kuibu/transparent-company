from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.connectors import get_connector
from app.api.utils import parse_period
from app.core.key_management import expected_signer_role
from app.core.security import Actor
from app.disclosure.publisher import publish_disclosure_run
from app.governance import PolicyEnforcementError
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore


class OrchestratorTask(BaseModel):
    task_id: str | None = None
    connector: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    max_retries: int = Field(default=1, ge=0, le=5)
    approvals: list[str] = Field(default_factory=list)


class DisclosureStep(BaseModel):
    policy_id: str
    period: str = Field(description="start/end in ISO8601")
    group_by: list[str] | None = None


class OrchestratorRunRequest(BaseModel):
    workflow_name: str = "agent-primary-drive"
    tasks: list[OrchestratorTask]
    max_concurrency: int = Field(default=4, ge=1, le=16)
    disclosure: DisclosureStep | None = None


class AgentOrchestrator:
    def __init__(self, session: Session):
        self.session = session
        self.ledger = LedgerStore(session)

    def _signer_for_actor(self, actor: Actor):
        return load_role_key(expected_signer_role(actor.type))

    def _emit_state(
        self,
        run_id: str,
        workflow_name: str,
        to_state: str,
        actor: Actor,
        signer,
        from_state: str | None,
        reason: str | None = None,
    ) -> None:
        req = EventCreateRequest(
            event_type="OrchestratorStateChanged",
            actor={"type": actor.type, "id": actor.id},
            policy_id="policy_internal_v1",
            payload={
                "run_id": run_id,
                "workflow_name": workflow_name,
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
            },
            tool_trace={"orchestrator": True},
        )
        self.ledger.append(req, signer=signer)

    def _emit_tool_log(self, run_id: str, task: OrchestratorTask, result: dict[str, Any], actor: Actor, signer) -> None:
        req = EventCreateRequest(
            event_type="ToolInvocationLogged",
            actor={"type": actor.type, "id": actor.id},
            policy_id="policy_internal_v1",
            payload={
                "run_id": run_id,
                "task_id": result["task_id"],
                "connector": task.connector,
                "action": task.action,
                "status": result["status"],
                "attempt": result["attempt"],
                "timeout_seconds": task.timeout_seconds,
                "max_retries": task.max_retries,
                "request_hash": result.get("request_hash", ""),
                "response_hash": result.get("response_hash"),
                "error": result.get("error"),
                "governance": result.get("governance", {}),
            },
            tool_trace={
                "connector": task.connector,
                "action": task.action,
                "duration_ms": result.get("duration_ms"),
            },
        )
        self.ledger.append(req, signer=signer)

    def _run_single_task(self, task: OrchestratorTask, actor: Actor, signer_role: str, task_id: str) -> dict[str, Any]:
        connector = get_connector(task.connector)
        last_error: str | None = None

        for attempt in range(1, task.max_retries + 2):
            started = time.monotonic()
            try:
                outcome = connector.invoke(
                    action=task.action,
                    payload=task.payload,
                    actor_type=actor.type,
                    signer_role=signer_role,
                    approvals=task.approvals,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                return {
                    "task_id": task_id,
                    "status": "success",
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                    "request_hash": outcome.request_hash,
                    "response_hash": outcome.response_hash,
                    "governance": outcome.governance,
                    "response": outcome.response,
                }
            except PolicyEnforcementError as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                return {
                    "task_id": task_id,
                    "status": "failed",
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                    "governance": {},
                }
            except Exception as exc:  # pragma: no cover - resilience branch
                last_error = str(exc)
                if attempt > task.max_retries:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    return {
                        "task_id": task_id,
                        "status": "failed",
                        "attempt": attempt,
                        "duration_ms": duration_ms,
                        "error": last_error,
                        "governance": {},
                    }

        return {
            "task_id": task_id,
            "status": "failed",
            "attempt": task.max_retries + 1,
            "duration_ms": 0,
            "error": last_error or "unknown error",
            "governance": {},
        }

    def run(self, request: OrchestratorRunRequest, actor: Actor) -> dict[str, Any]:
        run_id = str(uuid4())
        signer = self._signer_for_actor(actor)
        signer_role = signer.key_id

        current_state: str | None = None
        transitions: list[dict[str, Any]] = []

        def transition(to_state: str, reason: str | None = None) -> None:
            nonlocal current_state
            self._emit_state(
                run_id=run_id,
                workflow_name=request.workflow_name,
                to_state=to_state,
                from_state=current_state,
                reason=reason,
                actor=actor,
                signer=signer,
            )
            transitions.append(
                {
                    "from": current_state,
                    "to": to_state,
                    "reason": reason,
                    "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            )
            current_state = to_state

        transition("plan")

        tasks = request.tasks
        if not tasks:
            transition("failed", reason="no tasks provided")
            return {
                "run_id": run_id,
                "workflow_name": request.workflow_name,
                "status": "failed",
                "transitions": transitions,
                "tasks": [],
                "disclosure": None,
            }

        transition("act")

        results_by_id: dict[str, dict[str, Any]] = {}
        max_workers = min(request.max_concurrency, max(1, len(tasks)))

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Any, str] = {}
            task_lookup: dict[str, OrchestratorTask] = {}

            for idx, task in enumerate(tasks, start=1):
                task_id = task.task_id or f"task-{idx}"
                task_lookup[task_id] = task
                future = pool.submit(self._run_single_task, task, actor, signer_role, task_id)
                futures[future] = task_id

            for future, task_id in futures.items():
                task = task_lookup[task_id]
                try:
                    result = future.result(timeout=task.timeout_seconds)
                except TimeoutError:
                    result = {
                        "task_id": task_id,
                        "status": "failed",
                        "attempt": 1,
                        "duration_ms": task.timeout_seconds * 1000,
                        "error": f"timeout after {task.timeout_seconds}s",
                        "governance": {},
                    }
                    future.cancel()
                except Exception as exc:  # pragma: no cover
                    result = {
                        "task_id": task_id,
                        "status": "failed",
                        "attempt": 1,
                        "duration_ms": 0,
                        "error": str(exc),
                        "governance": {},
                    }

                results_by_id[task_id] = result
                self._emit_tool_log(run_id, task, result, actor=actor, signer=signer)

        ordered_results = [results_by_id[(task.task_id or f"task-{idx + 1}")] for idx, task in enumerate(tasks)]

        transition("verify")
        failures = [item for item in ordered_results if item["status"] != "success"]
        if failures:
            transition("failed", reason=f"{len(failures)} task(s) failed")
            return {
                "run_id": run_id,
                "workflow_name": request.workflow_name,
                "status": "failed",
                "transitions": transitions,
                "tasks": ordered_results,
                "disclosure": None,
            }

        transition("disclose")
        disclosure_result: dict[str, Any] | None = None
        if request.disclosure is not None:
            period_start, period_end = parse_period(request.disclosure.period)
            publish_result = publish_disclosure_run(
                session=self.session,
                policy_id=request.disclosure.policy_id,
                period_start=period_start,
                period_end=period_end,
                group_by=request.disclosure.group_by,
                actor=actor,
            )
            disclosure_result = {
                "disclosure_id": publish_result.disclosure_id,
                "policy_id": publish_result.payload["policy_id"],
                "root_summary": publish_result.payload["root_summary"],
                "root_details": publish_result.payload["root_details"],
            }

        transition("completed")

        return {
            "run_id": run_id,
            "workflow_name": request.workflow_name,
            "status": "completed",
            "transitions": transitions,
            "tasks": ordered_results,
            "disclosure": disclosure_result,
        }
