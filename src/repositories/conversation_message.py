"""Repository for durable, native PydanticAI conversation messages."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation_message import ConversationMessage


class ConversationMessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_pending(
        self,
        *,
        conversation_id: int,
        user_id: int,
        content: str,
        meta: dict | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=str(uuid.uuid4()),
            message_index=0,
            status="pending",
            role="user",
            content=content,
            message_data=None,
            meta=meta or {},
        )
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def claim(
        self, message_id: int, *, retry: bool = False
    ) -> ConversationMessage:
        message = await self.db.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.id == message_id)
            .with_for_update()
        )
        if message is None:
            raise LookupError("message not found")
        expected = "failed" if retry else "pending"
        if message.status != expected:
            raise ValueError(f"message status is {message.status}, expected {expected}")
        message.status = "running"
        message.error = None
        message.updated_at = datetime.now(UTC)
        if retry:
            message.retry_count += 1
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def complete_turn(
        self,
        *,
        pending_id: int,
        native_messages: list[tuple[str, str, dict]],
    ) -> ConversationMessage:
        """Atomically replace the pending row and append the rest of the native run."""
        if not native_messages:
            raise ValueError("agent returned no messages")
        pending = await self.db.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.id == pending_id)
            .with_for_update()
        )
        if pending is None or pending.status != "running":
            raise RuntimeError("pending message is missing or no longer running")

        first_role, first_content, first_data = native_messages[0]
        pending.role = first_role
        pending.content = first_content or pending.content
        pending.message_data = first_data
        pending.status = "completed"
        pending.error = None
        pending.updated_at = datetime.now(UTC)

        final_message = pending
        for index, (role, content, data) in enumerate(native_messages[1:], start=1):
            final_message = ConversationMessage(
                conversation_id=pending.conversation_id,
                user_id=pending.user_id,
                turn_id=pending.turn_id,
                message_index=index,
                status="completed",
                role=role,
                content=content,
                message_data=data,
                meta={},
            )
            self.db.add(final_message)
        await self.db.commit()
        await self.db.refresh(final_message)
        return final_message

    async def mark_failed(self, message_id: int, error: str) -> None:
        message = await self.db.get(ConversationMessage, message_id)
        if message is not None and message.status == "running":
            message.status = "failed"
            message.error = error[:2000]
            message.updated_at = datetime.now(UTC)
            await self.db.commit()

    async def get_by_id(self, message_id: int) -> ConversationMessage | None:
        return await self.db.get(ConversationMessage, message_id)

    async def list_by_conversation(
        self,
        *,
        conversation_id: int,
        after_id: int | None = None,
        before_id: int | None = None,
        limit: int = 100,
    ) -> list[ConversationMessage]:
        stmt = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
        )
        if after_id is not None:
            stmt = stmt.where(ConversationMessage.id > after_id)
        if before_id is not None:
            stmt = stmt.where(ConversationMessage.id < before_id)
        result = await self.db.execute(
            stmt.order_by(ConversationMessage.id.asc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_native_history(
        self,
        *,
        conversation_id: int,
        after_id: int | None,
        before_id: int,
    ) -> list[ConversationMessage]:
        stmt = select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.id < before_id,
            ConversationMessage.status == "completed",
            ConversationMessage.message_data.is_not(None),
        )
        if after_id is not None:
            stmt = stmt.where(ConversationMessage.id > after_id)
        result = await self.db.execute(stmt.order_by(ConversationMessage.id.asc()))
        return list(result.scalars().all())
