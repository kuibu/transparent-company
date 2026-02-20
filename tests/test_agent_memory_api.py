from __future__ import annotations

from app.core.config import get_settings


def test_agent_memory_chat_and_search(client):
    settings = get_settings()
    prev_backend = settings.agent_memory_backend
    settings.agent_memory_backend = "local"

    try:
        profile = client.post(
            "/agent/memory/profiles",
            json={
                "agent_id": "agent-ceo-001",
                "display_name": "透明公司CEO",
                "mission": "让无公害果蔬供应链更透明、更高效",
                "system_prompt": "你是公司CEO，所有决策必须兼顾现金流、质量与可追溯。",
                "metadata": {"department": "executive"},
            },
        )
        assert profile.status_code == 200

        conv = client.post(
            "/agent/memory/conversations",
            json={
                "agent_id": "agent-ceo-001",
                "counterpart_type": "human",
                "counterpart_id": "human-ops-001",
            },
        )
        assert conv.status_code == 200
        conversation_id = conv.json()["conversation_id"]

        decision = client.post(
            f"/agent/memory/conversations/{conversation_id}/messages",
            json={
                "sender_type": "agent",
                "sender_id": "agent-ceo-001",
                "content": "决策：本周优先清理番茄库存，线上渠道做限时促销。",
                "is_decision": True,
                "metadata": {"topic": "inventory"},
            },
        )
        assert decision.status_code == 200

        reply = client.post(
            f"/agent/memory/conversations/{conversation_id}/chat",
            json={
                "speaker_agent_id": "agent-ceo-001",
                "user_sender_type": "human",
                "user_sender_id": "human-ops-001",
                "user_message": "今天要怎么安排番茄销售策略？",
                "record_reply_as_decision": True,
            },
        )
        assert reply.status_code == 200
        reply_payload = reply.json()
        assert "使命" in reply_payload["assistant_text"]
        assert "透明" in reply_payload["assistant_text"]
        assert reply_payload["backend"]["active_backend"] == "local"

        search = client.get(
            f"/agent/memory/conversations/{conversation_id}/memory/search",
            params={"q": "番茄库存促销", "limit": 5},
        )
        assert search.status_code == 200
        hits = search.json()["hits"]
        assert hits
        assert any("番茄" in item["text"] for item in hits)

        detail = client.get(f"/agent/memory/conversations/{conversation_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["conversation"]["agent_id"] == "agent-ceo-001"
        assert len(data["messages"]) >= 4

        commit = client.post(f"/agent/memory/conversations/{conversation_id}/commit")
        assert commit.status_code == 200
        assert commit.json()["status"] == "ok"
    finally:
        settings.agent_memory_backend = prev_backend
