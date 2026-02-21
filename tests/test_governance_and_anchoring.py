from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.governance import get_governance_engine
from app.ledger.anchoring import AnchoringService


class BrokenAnchorClient:
    backend = "broken"

    def set(self, key: str, value: dict):  # pragma: no cover - always raises
        raise RuntimeError("broken anchor backend")


def test_governance_default_deny_for_unknown_action():
    decision = get_governance_engine().evaluate(
        action="tool:unknown.connector_action",
        actor_type="agent",
        signer_role="agent",
        payload={},
        tool_trace={},
        approvals=[],
    )
    assert decision.allowed is False
    assert "denied by default" in decision.reason


def test_anchoring_strict_mode_fails_closed(session):
    settings = get_settings()
    prev_strict = settings.anchor_strict
    settings.anchor_strict = True
    try:
        with pytest.raises(RuntimeError):
            AnchoringService(session, client=BrokenAnchorClient()).anchor_receipt(
                receipt_hash="abc",
                object_key="obj",
                source="test",
                occurred_at="2025-01-01T00:00:00Z",
            )
    finally:
        settings.anchor_strict = prev_strict


def test_anchoring_non_strict_can_fallback_to_fake(session):
    settings = get_settings()
    prev_strict = settings.anchor_strict
    settings.anchor_strict = False
    try:
        payload = AnchoringService(session, client=BrokenAnchorClient()).anchor_receipt(
            receipt_hash="def",
            object_key="obj",
            source="test",
            occurred_at="2025-01-01T00:00:00Z",
        )
        assert payload["backend"] == "fake"
    finally:
        settings.anchor_strict = prev_strict
