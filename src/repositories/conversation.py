"""Conversation repository."""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation import Conversation


class ConversationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, id: int) -> Conversation | None:
        return await self.db.get(Conversation, id)

    async def get_by_session_id(
        self, *, session_id: str, user_id: int
    ) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(
                Conversation.session_id == session_id,
                Conversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        session_id: str,
        user_id: int,
        title: str = "New Chat",
    ) -> tuple[Conversation, bool]:
        existing = await self.get_by_session_id(session_id=session_id, user_id=user_id)
        if existing:
            return existing, False

        conv = Conversation(
            session_id=session_id,
            user_id=user_id,
            title=title,
            status="active",
            meta={},
        )
        self.db.add(conv)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv, True

    async def list_by_user(
        self,
        *,
        user_id: int,
        status: str = "active",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[int, list[Conversation]]:
        base = select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.status == status,
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        rows = await self.db.execute(
            base.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
        )
        return total, list(rows.scalars().all())

    async def update_title(self, conv: Conversation, title: str) -> Conversation:
        conv.title = title
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def update_summary(
        self,
        conv: Conversation,
        *,
        summary: str | None,
        until_message_id: int | None = None,
        until_created_at: datetime | None = None,
    ) -> Conversation:
        conv.summary = summary
        if until_message_id is not None:
            conv.summary_until_message_id = until_message_id
        if until_created_at is not None:
            conv.summary_until_created_at = until_created_at
        conv.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def compare_and_set_summary(
        self,
        conv: Conversation,
        *,
        expected_message_id: int | None,
        summary: str,
        until_message_id: int,
        until_created_at: datetime,
    ) -> bool:
        """Update a summary only if another request has not advanced it."""
        condition = Conversation.summary_until_message_id.is_(None)
        if expected_message_id is not None:
            condition = Conversation.summary_until_message_id == expected_message_id
        result = await self.db.execute(
            update(Conversation)
            .where(Conversation.id == conv.id, condition)
            .values(
                summary=summary,
                summary_until_message_id=until_message_id,
                summary_until_created_at=until_created_at,
                updated_at=datetime.now(UTC),
            )
        )
        await self.db.commit()
        return bool(result.rowcount)

    async def archive(self, conv: Conversation) -> Conversation:
        conv.status = "archived"
        conv.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def soft_delete(self, conv: Conversation) -> Conversation:
        conv.status = "deleted"
        conv.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def touch(self, conv: Conversation) -> Conversation:
        conv.updated_at = datetime.now(UTC)
        await self.db.commit()
        return conv
