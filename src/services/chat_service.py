"""PydanticAI-native multi-turn chat orchestration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi import Request
from pydantic_ai import AgentRunResultEvent, capture_run_messages
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.ui import NativeEvent
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import get_agent
from agent.prompts import default_system_prompt
from db.session import AsyncSessionLocal
from repositories.chat import ChatRepository, flatten_batches

logger = logging.getLogger(__name__)


def current_run_messages(
    messages: list[ModelMessage],
) -> tuple[str | None, list[ModelMessage]]:
    run_id = next(
        (message.run_id for message in reversed(messages) if message.run_id),
        None,
    )
    if run_id is None:
        return None, []
    return run_id, [message for message in messages if message.run_id == run_id]


def to_ui_messages(
    messages: list[ModelMessage],
    *,
    run_id: str,
    created_at: str | None,
) -> list[dict]:
    interrupted = any(
        isinstance(message, (ModelRequest, ModelResponse))
        and message.state != "complete"
        for message in messages
    )
    result: list[dict] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    result.append(
                        {
                            "role": "user",
                            "content": part.content,
                            "run_id": run_id,
                            "interrupted": interrupted,
                            "created_at": created_at,
                        }
                    )
        elif isinstance(message, ModelResponse):
            content = "".join(
                part.content for part in message.parts if isinstance(part, TextPart)
            )
            if content:
                result.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "run_id": run_id,
                        "interrupted": interrupted,
                        "created_at": created_at,
                    }
                )
    return result


class ChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repository = ChatRepository(db)

    async def create_session(self, user_id: int) -> dict:
        session = await self.repository.create_session(
            session_id=str(uuid.uuid4()), user_id=user_id
        )
        return {
            "session_id": session.session_id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
        }

    async def list_sessions(self, user_id: int) -> list[dict]:
        sessions = await self.repository.list_sessions(user_id=user_id)
        return [
            {
                "session_id": session.session_id,
                "title": session.title,
                "status": session.status,
                "updated_at": session.updated_at.isoformat(),
            }
            for session in sessions
        ]

    async def set_session_status(
        self, *, session_id: str, user_id: int, status: str
    ) -> bool:
        return await self.repository.set_session_status(
            session_id=session_id, user_id=user_id, status=status
        )

    async def list_messages(self, *, session_id: str, user_id: int) -> list[dict]:
        rows = await self.repository.list_runs(
            session_id=session_id, user_id=user_id
        )
        result: list[dict] = []
        for row in rows:
            messages = ModelMessagesTypeAdapter.validate_python(row.message_list)
            result.extend(
                to_ui_messages(
                    messages,
                    run_id=row.run_id,
                    created_at=row.created_at.isoformat() if row.created_at else None,
                )
            )
        return result

    async def stream_native_events(
        self,
        *,
        request: Request,
        session_id: str,
        user_id: int,
        prompt: str,
    ) -> AsyncIterator[NativeEvent]:
        context = await self.repository.load_context(
            session_id=session_id, user_id=user_id
        )
        await self.db.rollback()
        history = [*context.summary_messages, *flatten_batches(context.batches)]
        final_result = None
        captured_messages: list[ModelMessage] = []

        try:
            with capture_run_messages() as captured_messages:
                async with get_agent().run_stream_events(
                    prompt,
                    message_history=history,
                    conversation_id=session_id,
                    instructions=default_system_prompt(),
                ) as events:
                    async for event in events:
                        if await request.is_disconnected():
                            break
                        if isinstance(event, AgentRunResultEvent):
                            final_result = event.result
                        yield event
        finally:
            if final_result is not None:
                new_messages = final_result.new_messages()
                run_id, new_messages = current_run_messages(new_messages)
                if run_id and new_messages:
                    await self.repository.commit_run(
                        session_id=session_id,
                        run_id=run_id,
                        new_messages=new_messages,
                    )
            else:
                run_id, new_messages = current_run_messages(captured_messages)
                if run_id and new_messages:
                    await self._persist_interrupted(
                        session_id=session_id,
                        run_id=run_id,
                        messages=new_messages,
                    )

    @staticmethod
    async def _persist_interrupted(
        *, session_id: str, run_id: str, messages: list[ModelMessage]
    ) -> None:
        async def persist() -> None:
            async with AsyncSessionLocal() as db:
                await ChatRepository(db).commit_run(
                    session_id=session_id,
                    run_id=run_id,
                    new_messages=messages,
                )

        task = asyncio.create_task(persist())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            with suppress(Exception):
                await task
            raise
        except Exception:
            logger.exception("Failed to persist interrupted run %s", run_id)
