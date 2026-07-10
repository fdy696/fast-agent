"""ChatSession repository."""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat_session import ChatSession


class ChatSessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, id: int) -> ChatSession | None:
        return await self.db.get(ChatSession, id)

    async def get_by_session_id(
        self, *, session_id: str, user_id: int
    ) -> ChatSession | None:
        result = await self.db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, *, session_id: str, user_id: int, title: str = "New Chat"
    ) -> ChatSession:
        session = ChatSession(
            session_id=session_id, user_id=user_id, title=title, status="active"
        )
        self.db.add(session)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            result = await self.db.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )
            return result.scalar_one()
        await self.db.refresh(session)
        return session

    async def list_by_user(
        self,
        *,
        user_id: int,
        status: str = "active",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[int, list[ChatSession]]:
        base = select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.status == status,
        )
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()
        rows = await self.db.execute(
            base.order_by(ChatSession.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return total, list(rows.scalars().all())

    async def update_title(self, session: ChatSession, title: str) -> ChatSession:
        session.title = title
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def update_summary(
        self,
        session: ChatSession,
        *,
        summary: str | None,
        until_message_id: int | None = None,
    ) -> ChatSession:
        session.history_summary = summary
        if until_message_id is not None:
            session.summarized_through_message_id = until_message_id
        session.summary_updated_at = datetime.now(timezone.utc)
        session.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def archive(self, session: ChatSession) -> ChatSession:
        session.status = "archived"
        session.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def soft_delete(self, session: ChatSession) -> ChatSession:
        session.status = "deleted"
        session.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def touch(self, session: ChatSession) -> ChatSession:
        session.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return session
