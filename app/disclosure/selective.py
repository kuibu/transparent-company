from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.key_management import expected_signer_role
from app.core.security import Actor, create_one_time_token, require_roles, verify_one_time_token
from app.disclosure.commitment import proof_lookup_key
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.models import (
    DisclosureRunModel,
    SelectiveRevealAuditModel,
    SelectiveRevealTokenModel,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SelectiveDisclosureService:
    def __init__(self, session: Session):
        self.session = session

    def request_token(self, disclosure_id: str, subject: str, actor: Actor) -> dict:
        require_roles(actor, {"human", "auditor"}, detail="selective disclosure token requires human/auditor role")

        ttl = get_settings().reveal_token_ttl_seconds
        now = datetime.now(timezone.utc)
        token_id = str(uuid4())
        token = create_one_time_token(
            subject=subject,
            disclosure_id=disclosure_id,
            ttl_seconds=ttl,
            token_id=token_id,
            issued_to_actor_type=actor.type,
            issued_to_actor_id=actor.id,
        )
        self.session.add(
            SelectiveRevealTokenModel(
                token_id=token_id,
                disclosure_id=disclosure_id,
                subject=subject,
                issued_to_actor_type=actor.type,
                issued_to_actor_id=actor.id,
                expires_at=now + timedelta(seconds=ttl),
                used_at=None,
                created_at=now,
            )
        )
        return {
            "disclosure_id": disclosure_id,
            "challenge": "sign-or-present-token",
            "token": token,
            "token_id": token_id,
            "subject": subject,
            "issued_to": {"type": actor.type, "id": actor.id},
            "expires_in_seconds": ttl,
        }

    def reveal(
        self,
        disclosure_id: str,
        token: str,
        metric_key: str,
        group: dict,
        actor: Actor,
    ) -> dict:
        require_roles(actor, {"human", "auditor"}, detail="selective disclosure reveal requires human/auditor role")

        claims = verify_one_time_token(token, disclosure_id)
        token_id = str(claims.get("jti", "")).strip()
        if not token_id:
            raise HTTPException(status_code=401, detail="token missing jti")

        run = self.session.get(DisclosureRunModel, disclosure_id)
        if run is None:
            raise HTTPException(status_code=404, detail="disclosure not found")

        token_row = self.session.get(SelectiveRevealTokenModel, token_id)
        if token_row is None:
            raise HTTPException(status_code=401, detail="token not issued")
        if token_row.disclosure_id != disclosure_id:
            raise HTTPException(status_code=401, detail="token scope mismatch")
        if token_row.used_at is not None:
            raise HTTPException(status_code=409, detail="token already used")

        now = datetime.now(timezone.utc)
        if _as_utc(token_row.expires_at) < now:
            raise HTTPException(status_code=401, detail="token expired")

        if token_row.issued_to_actor_type != actor.type or token_row.issued_to_actor_id != actor.id:
            raise HTTPException(status_code=403, detail="token actor mismatch")

        lookup = proof_lookup_key(metric_key, group)
        detail = run.detail_index.get(lookup)
        if not detail:
            raise HTTPException(status_code=404, detail="no selective detail for given metric/group")

        event_hashes = detail.get("event_hashes", [])
        response = {
            "disclosure_id": disclosure_id,
            "metric_key": metric_key,
            "group": group,
            "detail_root": detail.get("detail_root"),
            "root_details": run.root_details,
            "revealed_event_hashes": event_hashes,
            "event_proofs": detail.get("event_proofs", {}),
        }

        token_row.used_at = now

        audit = SelectiveRevealAuditModel(
            disclosure_id=disclosure_id,
            actor_type=actor.type,
            actor_id=actor.id,
            challenge_subject=claims.get("subject", "unknown"),
            requested_metric_key=metric_key,
            requested_group=group,
            granted=True,
            created_at=now,
        )
        self.session.add(audit)

        # Persist reveal as an immutable ledger event for traceability.
        req = EventCreateRequest(
            event_type="SelectiveDisclosureRevealed",
            actor={"type": actor.type if actor.type in {"human", "system", "agent", "auditor"} else "system", "id": actor.id},
            policy_id=run.policy_id,
            payload={
                "disclosure_id": disclosure_id,
                "metric_key": metric_key,
                "group": group,
                "revealed_event_hashes": event_hashes,
                "challenge_subject": claims.get("subject", "unknown"),
            },
            tool_trace={"token_exp": claims.get("exp"), "token_id": token_id},
        )
        LedgerStore(self.session).append(req, signer=load_role_key(expected_signer_role(actor.type)))

        return response
