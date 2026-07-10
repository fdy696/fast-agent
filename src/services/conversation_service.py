"""
会话服务 —— 会话 CRUD + 消息列表。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from repositories import ConversationMessageRepository, ConversationRepository


class ConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.conv_repo = ConversationRepository(db)
        self.msg_repo = ConversationMessageRepository(db)

    async def create(self, *, user_id: int, title: str = "New Chat") -> dict:
        session_id = str(uuid.uuid4())
        conv, created = await self.conv_repo.get_or_create(
            session_id=session_id, user_id=user_id, title=title
        )
        return {"session_id": conv.session_id, "id": conv.id, "created": created}

    async def list_by_user(self, *, user_id: int) -> list[dict]:
        _, convs = await self.conv_repo.list_by_user(user_id=user_id)
        return [
            {
                "id": c.id,
                "session_id": c.session_id,
                "title": c.title,
                "status": c.status,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in convs
        ]

    async def get_messages(
        self, *, session_id: str, user_id: int, after_id: int | None = None
    ) -> list[dict]:
        conv = await self.conv_repo.get_by_session_id(
            session_id=session_id, user_id=user_id
        )
        if not conv:
            return []
        msgs = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, after_id=after_id
        )
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "turn_id": m.turn_id,
                "status": m.status,
                "error": m.error,
                "retry_count": m.retry_count,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ]

    async def archive(self, *, session_id: str, user_id: int) -> None:
        conv = await self.conv_repo.get_by_session_id(
            session_id=session_id, user_id=user_id
        )
        if conv:
            await self.conv_repo.archive(conv)

    async def soft_delete(self, *, session_id: str, user_id: int) -> None:
        conv = await self.conv_repo.get_by_session_id(
            session_id=session_id, user_id=user_id
        )
        if conv:
            await self.conv_repo.soft_delete(conv)
