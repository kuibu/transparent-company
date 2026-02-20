from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.memory_backend import (
    LocalMemoryBackend,
    MemorySearchHit,
    OpenVikingHTTPMemoryBackend,
    build_memory_backend,
)
from app.core.config import get_settings
from app.persistence.models import AgentConversationModel, AgentMessageModel, AgentProfileModel


SenderType = Literal["agent", "human", "auditor", "system"]


@dataclass
class BackendStatus:
    requested_backend: str
    active_backend: str
    healthy: bool
    detail: str


@dataclass
class AgentReply:
    conversation_id: str
    user_message_id: int
    assistant_message_id: int
    assistant_text: str
    mission: str
    memory_hits: list[dict[str, Any]]
    backend_status: BackendStatus


class AgentChatService:
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()
        self.default_backend = build_memory_backend(self.settings)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _role_for_sender(sender_type: SenderType) -> str:
        return "user" if sender_type in {"human", "auditor"} else "assistant"

    def _backend_for_name(self, name: str):
        if name == "openviking_http":
            return OpenVikingHTTPMemoryBackend(self.settings)
        return LocalMemoryBackend()

    def upsert_profile(
        self,
        agent_id: str,
        mission: str,
        system_prompt: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentProfileModel:
        row = self.session.get(AgentProfileModel, agent_id)
        now = self._utcnow()
        if row is None:
            row = AgentProfileModel(
                agent_id=agent_id,
                mission=mission,
                system_prompt=system_prompt,
                display_name=display_name,
                metadata_json=metadata or {},
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row.mission = mission
            row.system_prompt = system_prompt
            row.display_name = display_name
            row.metadata_json = metadata or {}
            row.updated_at = now
        self.session.flush()
        return row

    def get_profile(self, agent_id: str) -> AgentProfileModel | None:
        return self.session.get(AgentProfileModel, agent_id)

    def create_conversation(
        self,
        agent_id: str,
        counterpart_type: Literal["human", "agent", "auditor"],
        counterpart_id: str,
        conversation_id: str | None = None,
        seed_system_prompt: bool = True,
    ) -> AgentConversationModel:
        if not conversation_id:
            conversation_id = uuid4().hex

        existing = self.session.get(AgentConversationModel, conversation_id)
        if existing is not None:
            return existing

        profile = self.get_profile(agent_id)
        if profile is None:
            raise ValueError(f"unknown agent profile: {agent_id}")

        now = self._utcnow()
        preferred_backend = self.default_backend

        backend_name = preferred_backend.backend_name
        memory_session_id: str | None = None

        health = preferred_backend.health()
        if health.healthy:
            try:
                memory_session_id = preferred_backend.create_session(preferred_session_id=conversation_id)
            except Exception:
                if self.settings.openviking_fallback_local:
                    backend_name = "local"
                    memory_session_id = LocalMemoryBackend().create_session(preferred_session_id=conversation_id)
                else:
                    raise
        else:
            if backend_name == "openviking_http" and self.settings.openviking_fallback_local:
                backend_name = "local"
                memory_session_id = LocalMemoryBackend().create_session(preferred_session_id=conversation_id)
            else:
                raise RuntimeError(f"memory backend unavailable: {health.detail}")

        conversation = AgentConversationModel(
            conversation_id=conversation_id,
            agent_id=agent_id,
            counterpart_type=counterpart_type,
            counterpart_id=counterpart_id,
            memory_backend=backend_name,
            memory_session_id=memory_session_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(conversation)
        self.session.flush()

        if seed_system_prompt:
            self.add_message(
                conversation_id=conversation_id,
                sender_type="system",
                sender_id=agent_id,
                content=f"SYSTEM_PROMPT: {profile.system_prompt}",
                is_decision=False,
                metadata={"kind": "system_prompt_seed"},
            )

        return conversation

    def _fallback_to_local(self, conversation: AgentConversationModel, reason: str) -> None:
        conversation.memory_backend = "local"
        if not conversation.memory_session_id:
            conversation.memory_session_id = LocalMemoryBackend().create_session(preferred_session_id=conversation.conversation_id)
        conversation.updated_at = self._utcnow()
        self.add_message(
            conversation_id=conversation.conversation_id,
            sender_type="system",
            sender_id="system-memory-fallback",
            content=f"Memory backend switched to local fallback: {reason}",
            is_decision=False,
            metadata={"kind": "memory_backend_fallback", "reason": reason},
            sync_to_memory_backend=False,
        )

    def add_message(
        self,
        conversation_id: str,
        sender_type: SenderType,
        sender_id: str,
        content: str,
        is_decision: bool = False,
        metadata: dict[str, Any] | None = None,
        sync_to_memory_backend: bool = True,
    ) -> AgentMessageModel:
        conversation = self.session.get(AgentConversationModel, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation not found: {conversation_id}")

        role = self._role_for_sender(sender_type)
        now = self._utcnow()

        msg = AgentMessageModel(
            conversation_id=conversation_id,
            sender_type=sender_type,
            sender_id=sender_id,
            role=role,
            content=content,
            is_decision=is_decision,
            metadata_json=metadata or {},
            created_at=now,
        )
        self.session.add(msg)
        conversation.updated_at = now
        self.session.flush()

        if sync_to_memory_backend and conversation.memory_session_id:
            backend = self._backend_for_name(conversation.memory_backend)
            try:
                backend.add_message(
                    session_id=conversation.memory_session_id,
                    role=role,
                    content=content,
                )
            except Exception as exc:
                if conversation.memory_backend == "openviking_http" and self.settings.openviking_fallback_local:
                    self._fallback_to_local(conversation, str(exc))
                else:
                    raise

        return msg

    def commit_memory(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.session.get(AgentConversationModel, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation not found: {conversation_id}")
        backend = self._backend_for_name(conversation.memory_backend)

        if not conversation.memory_session_id:
            return {"status": "skipped", "reason": "no memory session"}

        try:
            result = backend.commit(conversation.memory_session_id)
            return {
                "status": "ok",
                "backend": conversation.memory_backend,
                "memory_session_id": conversation.memory_session_id,
                "result": result,
            }
        except Exception as exc:
            if conversation.memory_backend == "openviking_http" and self.settings.openviking_fallback_local:
                self._fallback_to_local(conversation, str(exc))
                local = LocalMemoryBackend().commit(conversation.memory_session_id)
                return {
                    "status": "ok",
                    "backend": "local",
                    "memory_session_id": conversation.memory_session_id,
                    "result": local,
                    "fallback_reason": str(exc),
                }
            raise

    def _local_search(self, conversation_id: str, query: str, limit: int = 5) -> list[MemorySearchHit]:
        query_terms = [w for w in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", query.lower()) if len(w) >= 2]
        query_chars = {ch for ch in query.lower() if not ch.isspace()}
        rows = list(
            self.session.scalars(
                select(AgentMessageModel)
                .where(AgentMessageModel.conversation_id == conversation_id)
                .order_by(AgentMessageModel.id.desc())
                .limit(400)
            ).all()
        )

        scored: list[tuple[float, AgentMessageModel]] = []
        for row in rows:
            text = row.content.lower()
            overlap = sum(1 for term in query_terms if term in text)
            char_overlap = len(query_chars.intersection({ch for ch in text if not ch.isspace()}))
            if overlap == 0 and char_overlap == 0 and query_terms:
                continue
            recency_bonus = max(0.0, 0.5 - (len(scored) * 0.001))
            decision_bonus = 1.0 if row.is_decision else 0.0
            score = float(overlap) + (0.05 * float(char_overlap)) + decision_bonus + recency_bonus
            scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)

        hits: list[MemorySearchHit] = []
        for score, row in scored[:limit]:
            hits.append(
                MemorySearchHit(
                    text=row.content,
                    score=score,
                    uri=f"local://conversation/{conversation_id}/messages/{row.id}",
                    metadata={
                        "message_id": row.id,
                        "sender_type": row.sender_type,
                        "is_decision": row.is_decision,
                        "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
                    },
                )
            )
        return hits

    def search_memory(self, conversation_id: str, query: str, limit: int = 5) -> tuple[list[MemorySearchHit], BackendStatus]:
        conversation = self.session.get(AgentConversationModel, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation not found: {conversation_id}")

        requested_backend = conversation.memory_backend
        backend = self._backend_for_name(requested_backend)

        if requested_backend == "openviking_http" and conversation.memory_session_id:
            health = backend.health()
            if health.healthy:
                try:
                    hits = backend.search(query=query, session_id=conversation.memory_session_id, limit=limit)
                    if hits:
                        return (
                            hits,
                            BackendStatus(
                                requested_backend=requested_backend,
                                active_backend=requested_backend,
                                healthy=True,
                                detail="openviking search success",
                            ),
                        )
                except Exception as exc:
                    if self.settings.openviking_fallback_local:
                        self._fallback_to_local(conversation, str(exc))
                    else:
                        raise

        local_hits = self._local_search(conversation_id, query, limit)
        return (
            local_hits,
            BackendStatus(
                requested_backend=requested_backend,
                active_backend="local",
                healthy=True,
                detail="local memory search",
            ),
        )

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.session.get(AgentConversationModel, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation not found: {conversation_id}")
        profile = self.get_profile(conversation.agent_id)

        rows = list(
            self.session.scalars(
                select(AgentMessageModel)
                .where(AgentMessageModel.conversation_id == conversation_id)
                .order_by(AgentMessageModel.id.asc())
            ).all()
        )

        return {
            "conversation": {
                "conversation_id": conversation.conversation_id,
                "agent_id": conversation.agent_id,
                "counterpart_type": conversation.counterpart_type,
                "counterpart_id": conversation.counterpart_id,
                "memory_backend": conversation.memory_backend,
                "memory_session_id": conversation.memory_session_id,
                "created_at": conversation.created_at.isoformat().replace("+00:00", "Z"),
                "updated_at": conversation.updated_at.isoformat().replace("+00:00", "Z"),
            },
            "agent_profile": None
            if profile is None
            else {
                "agent_id": profile.agent_id,
                "display_name": profile.display_name,
                "mission": profile.mission,
                "system_prompt": profile.system_prompt,
            },
            "messages": [
                {
                    "id": row.id,
                    "sender_type": row.sender_type,
                    "sender_id": row.sender_id,
                    "role": row.role,
                    "content": row.content,
                    "is_decision": row.is_decision,
                    "metadata": row.metadata_json,
                    "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
                }
                for row in rows
            ],
        }

    def agent_reply(
        self,
        conversation_id: str,
        speaker_agent_id: str,
        user_sender_type: Literal["human", "agent", "auditor"],
        user_sender_id: str,
        user_message: str,
        record_reply_as_decision: bool = True,
        memory_limit: int = 3,
    ) -> AgentReply:
        conversation = self.session.get(AgentConversationModel, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation not found: {conversation_id}")

        profile = self.get_profile(speaker_agent_id)
        if profile is None:
            raise ValueError(f"unknown agent profile: {speaker_agent_id}")

        user_row = self.add_message(
            conversation_id=conversation_id,
            sender_type=user_sender_type,
            sender_id=user_sender_id,
            content=user_message,
            is_decision=False,
        )

        hits, backend_status = self.search_memory(conversation_id, user_message, limit=memory_limit)

        memory_lines = []
        for idx, hit in enumerate(hits, start=1):
            snippet = hit.text.strip().replace("\n", " ")
            if len(snippet) > 90:
                snippet = snippet[:90] + "..."
            memory_lines.append(f"{idx}. {snippet}")

        display_name = profile.display_name or profile.agent_id
        assistant_lines = [
            f"我是 {display_name}，当前角色是公司主驾驶（CEO agent）。",
            f"我的使命：{profile.mission}",
            f"你的输入：{user_message}",
        ]
        if memory_lines:
            assistant_lines.append("我记得这些相关决策/上下文：")
            assistant_lines.extend(memory_lines)
        assistant_lines.append("建议行动：先按使命对齐目标，再执行低风险试点并保留审计轨迹。")

        assistant_text = "\n".join(assistant_lines)

        assistant_row = self.add_message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id=speaker_agent_id,
            content=assistant_text,
            is_decision=record_reply_as_decision,
            metadata={
                "generated": True,
                "memory_backend": backend_status.active_backend,
                "memory_hit_count": len(hits),
            },
        )

        if self.settings.openviking_auto_commit:
            self.commit_memory(conversation_id)

        return AgentReply(
            conversation_id=conversation_id,
            user_message_id=user_row.id,
            assistant_message_id=assistant_row.id,
            assistant_text=assistant_text,
            mission=profile.mission,
            memory_hits=[
                {
                    "text": h.text,
                    "score": h.score,
                    "uri": h.uri,
                    "metadata": h.metadata,
                }
                for h in hits
            ],
            backend_status=backend_status,
        )
