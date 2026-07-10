"""ChatMessage repository — CRUD and history queries."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat_message import ChatMessage


class ChatMessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, message_id: int) -> ChatMessage | None:
        return await self.db.get(ChatMessage, message_id)

    async def get_by_id_for_update(self, message_id: int) -> ChatMessage | None:
        """Row-locked read for atomic status transitions."""
        result = await self.db.execute(
            select(ChatMessage).where(ChatMessage.id == message_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def insert(
        self,
        *,
        session_id: str,
        turn_id: str,
        message_index: int,
        status: str,
        role: str,
        content: str | None = None,
        message_data: dict | None = None,
        error_message: str | None = None,
        retry_count: int = 0,
    ) -> ChatMessage:
        msg = ChatMessage(
            session_id=session_id,
            turn_id=turn_id,
            message_index=message_index,
            status=status,
            role=role,
            content=content,
            message_data=message_data,
            error_message=error_message,
            retry_count=retry_count,
        )
        self.db.add(msg)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def update_status(
        self,
        msg: ChatMessage,
        status: str,
        *,
        error_message: str | None = None,
    ) -> ChatMessage:
        msg.status = status
        msg.updated_at = datetime.now(UTC)
        if error_message is not None:
            msg.error_message = error_message
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def update_message_data(
        self, msg: ChatMessage, *, message_data: dict
    ) -> ChatMessage:
        msg.message_data = message_data
        msg.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def cancel_running_messages(self, *, session_id: str) -> int:
        """Cancel all running and pending messages in a session."""
        tz = datetime.now(UTC)
        result = await self.db.execute(
            update(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.status.in_(["running", "pending"]),
            )
            .values(status="cancelled", updated_at=tz)
        )
        await self.db.commit()
        return result.rowcount

    async def cancel_if_running(self, message_id: int) -> bool:
        """Cancel a turn without overwriting a concurrently completed result."""
        result = await self.db.execute(
            update(ChatMessage)
            .where(
                ChatMessage.id == message_id,
                ChatMessage.status == "running",
            )
            .values(status="cancelled", updated_at=datetime.now(UTC))
        )
        await self.db.commit()
        return result.rowcount == 1

    async def list_by_session(
        self,
        *,
        session_id: str,
        after_id: int | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        stmt = select(ChatMessage).where(
            ChatMessage.session_id == session_id,
        )
        if after_id is not None:
            stmt = stmt.where(ChatMessage.id > after_id)
        stmt = stmt.order_by(ChatMessage.id.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_completed_turns(
        self,
        *,
        session_id: str,
        after_message_id: int | None = None,
        before_message_id: int | None = None,
    ) -> list[ChatMessage]:
        """Get completed messages for build_model_history, ordered by id."""
        stmt = select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.status == "completed",
            ChatMessage.message_data.isnot(None),
        )
        if after_message_id is not None:
            stmt = stmt.where(ChatMessage.id > after_message_id)
        if before_message_id is not None:
            stmt = stmt.where(ChatMessage.id < before_message_id)
        stmt = stmt.order_by(ChatMessage.id.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_turn(self, *, turn_id: str) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.turn_id == turn_id)
            .order_by(ChatMessage.message_index.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
