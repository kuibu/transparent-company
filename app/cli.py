from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agent.skills.executor import SkillExecutor
from app.core.config import get_settings
from app.core.security import Actor
from app.persistence.pg import init_db, session_scope


def _default_actor_id(actor_type: str) -> str:
    settings = get_settings()
    mapping = {
        "agent": settings.agent_actor_id,
        "human": settings.human_actor_id,
        "auditor": settings.auditor_actor_id,
        "system": settings.system_actor_id,
    }
    return mapping[actor_type]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transparent Company CLI")
    top = parser.add_subparsers(dest="command", required=True)

    agent = top.add_parser("agent", help="Agent/skill operations")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    run = agent_sub.add_parser("run", help="Route and execute a skill")
    run.add_argument("query", help="Natural language query, optionally prefixed with skill:<name>")
    run.add_argument("--actor-type", choices=["agent", "human", "auditor", "system"], default="agent")
    run.add_argument("--actor-id", default=None)
    run.add_argument("--skills-dir", default=None, help="Path to skills directory (default: settings.skills_root)")

    return parser


def _run_agent_skill(args: argparse.Namespace) -> int:
    init_db()
    actor_type = str(args.actor_type)
    actor_id = str(args.actor_id or _default_actor_id(actor_type))
    actor = Actor(type=actor_type, id=actor_id)

    with session_scope() as session:
        executor = SkillExecutor.from_settings(
            session=session,
            actor=actor,
            skills_root=Path(args.skills_dir) if args.skills_dir else None,
        )
        result = executor.run(args.query)
        print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "skill_name": result.skill_name,
                    "entrypoint": result.entrypoint,
                    "inputs_hash": result.inputs_hash,
                    "outputs_hash": result.outputs_hash,
                    "output": result.output,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "agent" and args.agent_command == "run":
        return _run_agent_skill(args)

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
