"""Persistence for chat sessions and complete PydanticAI run batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
)
from pydantic_core import to_jsonable_python
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat_message import ChatMessage
from models.chat_session import ChatSession


@dataclass(frozen=True)
class MessageBatch:
    message_id: int
    messages: list[ModelMessage]


@dataclass(frozen=True)
class ChatContext:
    summary_messages: list[ModelMessage]
    batches: list[MessageBatch]


@dataclass(frozen=True)
class SummaryContext:
    previous_summary: list[ModelMessage]
    old_cursor: int | None
    unsummarized_batches: list[MessageBatch]


def is_reusable_run(messages: list[ModelMessage]) -> bool:
    lifecycle = [
        message
        for message in messages
        if isinstance(message, (ModelRequest, ModelResponse))
    ]
    if not lifecycle or not all(message.state == "complete" for message in lifecycle):
        return False
    last = lifecycle[-1]
    return isinstance(last, ModelResponse) and any(
        isinstance(part, TextPart) and bool(part.content.strip())
        for part in last.parts
    )


def flatten_batches(batches: list[MessageBatch]) -> list[ModelMessage]:
    return [message for batch in batches for message in batch.messages]


class ChatRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, *, session_id: str, user_id: int) -> ChatSession:
        session = ChatSession(session_id=session_id, user_id=user_id)
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session(
        self, *, session_id: str, user_id: int
    ) -> ChatSession | None:
        session: ChatSession | None = await self.db.scalar(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        return session

    async def list_sessions(
        self, *, user_id: int, status: str = "active", limit: int = 20
    ) -> list[ChatSession]:
        rows = await self.db.scalars(
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.status == status)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        return list(rows.all())

    async def set_session_status(
        self, *, session_id: str, user_id: int, status: str
    ) -> bool:
        result = await self.db.execute(
            update(ChatSession)
            .where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
            .values(status=status, updated_at=datetime.now(UTC))
        )
        await self.db.commit()
        return cast(CursorResult[Any], result).rowcount == 1

    async def load_context(self, *, session_id: str, user_id: int) -> ChatContext:
        session = await self.get_session(session_id=session_id, user_id=user_id)
        if session is None:
            raise LookupError("chat session not found")

        summary_messages = (
            ModelMessagesTypeAdapter.validate_python(session.summary_message_list)
            if session.summary_message_list
            else []
        )
        conditions = [ChatMessage.session_id == session_id]
        if session.summarized_through_message_id is not None:
            conditions.append(ChatMessage.id > session.summarized_through_message_id)
        rows = await self.db.scalars(
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.id)
        )
        batches = []
        for row in rows.all():
            messages = ModelMessagesTypeAdapter.validate_python(row.message_list)
            if is_reusable_run(messages):
                batches.append(MessageBatch(row.id, messages))
        return ChatContext(summary_messages, batches)

    async def commit_run(
        self,
        *,
        session_id: str,
        run_id: str,
        new_messages: list[ModelMessage],
    ) -> int:
        stmt = (
            insert(ChatMessage)
            .values(
                session_id=session_id,
                run_id=run_id,
                message_list=to_jsonable_python(new_messages),
            )
            .on_conflict_do_nothing(index_elements=["session_id", "run_id"])
            .returning(ChatMessage.id)
        )
        message_id = await self.db.scalar(stmt)
        if message_id is None:
            message_id = await self.db.scalar(
                select(ChatMessage.id).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.run_id == run_id,
                )
            )
        await self.db.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(updated_at=datetime.now(UTC))
        )
        await self.db.commit()
        if message_id is None:
            raise RuntimeError("failed to persist chat run")
        return message_id

    async def list_runs(self, *, session_id: str, user_id: int) -> list[ChatMessage]:
        session = await self.get_session(session_id=session_id, user_id=user_id)
        if session is None:
            raise LookupError("chat session not found")
        rows = await self.db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id)
        )
        return list(rows.all())

    async def load_summary_context(self, session_id: str) -> SummaryContext | None:
        session = await self.db.scalar(
            select(ChatSession).where(ChatSession.session_id == session_id)
        )
        if session is None:
            return None
        previous_summary = (
            ModelMessagesTypeAdapter.validate_python(session.summary_message_list)
            if session.summary_message_list
            else []
        )
        conditions = [ChatMessage.session_id == session_id]
        if session.summarized_through_message_id is not None:
            conditions.append(ChatMessage.id > session.summarized_through_message_id)
        rows = await self.db.scalars(
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.id)
        )
        batches = []
        for row in rows.all():
            messages = ModelMessagesTypeAdapter.validate_python(row.message_list)
            if is_reusable_run(messages):
                batches.append(MessageBatch(row.id, messages))
        return SummaryContext(
            previous_summary,
            session.summarized_through_message_id,
            batches,
        )

    async def save_summary(
        self,
        *,
        session_id: str,
        summary_messages: list[ModelMessage],
        old_cursor: int | None,
        new_cursor: int,
    ) -> bool:
        cursor_condition = (
            ChatSession.summarized_through_message_id.is_(None)
            if old_cursor is None
            else ChatSession.summarized_through_message_id == old_cursor
        )
        result = await self.db.execute(
            update(ChatSession)
            .where(
                ChatSession.session_id == session_id,
                cursor_condition,
            )
            .values(
                summary_message_list=to_jsonable_python(summary_messages),
                summarized_through_message_id=new_cursor,
                updated_at=datetime.now(UTC),
            )
        )
        await self.db.commit()
        return cast(CursorResult[Any], result).rowcount == 1
