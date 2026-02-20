from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.key_management import expected_signer_role
from app.core.security import Actor, create_one_time_token, verify_one_time_token
from app.disclosure.commitment import proof_lookup_key
from app.ledger.events import EventCreateRequest
from app.ledger.signing import load_role_key
from app.ledger.store import LedgerStore
from app.persistence.models import DisclosureRunModel, SelectiveRevealAuditModel


class SelectiveDisclosureService:
    def __init__(self, session: Session):
        self.session = session

    def request_token(self, disclosure_id: str, subject: str) -> dict:
        ttl = get_settings().reveal_token_ttl_seconds
        token = create_one_time_token(subject=subject, disclosure_id=disclosure_id, ttl_seconds=ttl)
        return {
            "disclosure_id": disclosure_id,
            "challenge": "sign-or-present-token",
            "token": token,
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
        claims = verify_one_time_token(token, disclosure_id)
        run = self.session.get(DisclosureRunModel, disclosure_id)
        if run is None:
            raise HTTPException(status_code=404, detail="disclosure not found")

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

        audit = SelectiveRevealAuditModel(
            disclosure_id=disclosure_id,
            actor_type=actor.type,
            actor_id=actor.id,
            challenge_subject=claims.get("subject", "unknown"),
            requested_metric_key=metric_key,
            requested_group=group,
            granted=True,
            created_at=datetime.now(timezone.utc),
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
            tool_trace={"token_exp": claims.get("exp")},
        )
        LedgerStore(self.session).append(req, signer=load_role_key(expected_signer_role(actor.type)))

        return response
