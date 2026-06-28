"""Conversation message repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation_message import ConversationMessage


class ConversationMessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(
        self,
        *,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
        run_id: str | None = None,
        meta: dict | None = None,
    ) -> ConversationMessage:
        msg = ConversationMessage(
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            content=content,
            run_id=run_id,
            meta=meta or {},
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def get_by_id(self, message_id: int) -> ConversationMessage | None:
        return await self.db.get(ConversationMessage, message_id)

    async def list_by_conversation(
        self,
        *,
        conversation_id: int,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[ConversationMessage]:
        stmt = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
        )
        if after_id is not None:
            stmt = stmt.where(ConversationMessage.id > after_id)
        stmt = stmt.order_by(ConversationMessage.id.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_run(self, *, run_id: str) -> list[ConversationMessage]:
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.run_id == run_id)
            .order_by(ConversationMessage.id.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def bind_run(self, message_id: int, run_id: str) -> None:
        msg = await self.get_by_id(message_id)
        if msg:
            msg.run_id = run_id
            await self.db.commit()
