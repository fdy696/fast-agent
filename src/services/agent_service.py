"""Agent business orchestration.

Owns DB lifecycle, conversation/message/run state, and SSE mapping.
The runtime loop stays database-free.
"""

from __future__ import annotations

import uuid
import asyncio
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent import AgentEvent, AgentLoop, AgentLoopRequest
from agent.prompts import default_system_prompt
from agent.tools import create_default_tool_executor
from core.config import settings
from repositories import (
    AgentRunRepository,
    AgentStepRepository,
    ConversationMessageRepository,
    ConversationRepository,
    TokenUsageRepository,
)
from schemas.agent import ChatRequest


class AgentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.conv_repo = ConversationRepository(db)
        self.msg_repo = ConversationMessageRepository(db)
        self.run_repo = AgentRunRepository(db)
        self.step_repo = AgentStepRepository(db)
        self.usage_repo = TokenUsageRepository(db)

    async def run(self, payload: ChatRequest, user_id: int) -> dict[str, Any]:
        if payload.session_id is None:
            payload.session_id = str(uuid.uuid4())
        chunks: list[str] = []
        async for event in self.stream(payload, user_id):
            if event.type == "llm.delta":
                chunks.append(str(event.data.get("text", "")))
            elif event.type == "error":
                chunks.append(str(event.data.get("message", "")))
                break
        return {
            "answer": "".join(chunks),
            "session_id": payload.session_id,
            "model": settings.AGENT_MODEL,
            "tool_calls": [],
        }

    async def stream(self, payload: ChatRequest, user_id: int) -> AsyncIterator[AgentEvent]:
        session_id = payload.session_id or str(uuid.uuid4())
        run_id = self._new_run_id()

        conv, _ = await self.conv_repo.get_or_create(
            session_id=session_id,
            user_id=user_id,
        )
        user_msg = await self.msg_repo.insert(
            conversation_id=conv.id,
            user_id=user_id,
            role="user",
            content=payload.message,
            run_id=run_id,
            meta=self._message_meta(payload.metadata),
        )
        run = await self.run_repo.create(
            run_id=run_id,
            conversation_id=conv.id,
            user_id=user_id,
            model=settings.AGENT_MODEL,
            user_message_id=user_msg.id,
        )

        tool_executor = create_default_tool_executor()
        loop = AgentLoop(tool_executor=tool_executor)
        context = await self._build_context(conv.id)
        request = AgentLoopRequest(
            prompt=payload.message,
            system_prompt=self._build_system_prompt(
                payload.system_prompt or default_system_prompt(),
                context,
            ),
            context=context,
            tools=tool_executor.definitions(),
        )

        answer_parts: list[str] = []
        step_index = 0
        llm_step = await self.step_repo.insert(
            run_id=run_id,
            step_index=step_index,
            category="llm_call",
            type="model",
            name=settings.AGENT_MODEL,
            input={"prompt_length": len(payload.message)},
            status="running",
        )
        step_index += 1

        try:
            async for event in loop.stream(request):
                if event.type == "llm.delta":
                    text = str(event.data.get("text", ""))
                    answer_parts.append(text)
                    yield event
                elif event.type == "tool.call":
                    yield event
                elif event.type == "tool.result":
                    await self.step_repo.insert(
                        run_id=run_id,
                        step_index=step_index,
                        category="tool_call",
                        type="tool",
                        name=str(event.data.get("name", "unknown")),
                        output=self._compact_event_data(event),
                        status="succeeded" if event.data.get("ok") else "failed",
                        error=str(event.data.get("error")) if event.data.get("error") else None,
                    )
                    step_index += 1
                    yield event
                elif event.type == "done":
                    await self.step_repo.mark_done(
                        llm_step,
                        status="succeeded",
                        output={"answer_length": len("".join(answer_parts))},
                    )
                    yield event
                elif event.type == "error":
                    message = str(event.data.get("message", "Agent runtime error"))
                    await self.step_repo.mark_done(llm_step, status="failed", error=message)
                    await self.run_repo.mark_failed(run, error=message)
                    yield event
                    return

            full_answer = "".join(answer_parts)
            assistant_msg = await self.msg_repo.insert(
                conversation_id=conv.id,
                user_id=user_id,
                role="assistant",
                content=full_answer,
                run_id=run_id,
                meta=self._message_meta({"content_type": "markdown"}),
            )
            await self.run_repo.mark_succeeded(run, assistant_message_id=assistant_msg.id)
            await self.usage_repo.insert(
                user_id=user_id,
                conversation_id=conv.id,
                run_id=run_id,
                model=settings.AGENT_MODEL,
            )
            await self.conv_repo.touch(conv)
        except (GeneratorExit, asyncio.CancelledError):
            await self.run_repo.mark_cancelled(run)
            raise
        except Exception as exc:
            await self.step_repo.mark_done(llm_step, status="failed", error=str(exc))
            await self.run_repo.mark_failed(run, error=str(exc))
            raise

    async def _build_context(self, conversation_id: int) -> dict[str, Any]:
        messages = await self.msg_repo.list_by_conversation(
            conversation_id=conversation_id,
            limit=20,
        )
        return {
            "recent_messages": [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
        }

    @staticmethod
    def _build_system_prompt(base_prompt: str, context: dict[str, Any]) -> str:
        recent_messages = context.get("recent_messages") or []
        if not recent_messages:
            return base_prompt

        lines = [base_prompt, "", "Recent conversation context:"]
        for msg in recent_messages[-10:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
            if content:
                lines.append(f"{role}: {content[:1000]}")
        return "\n".join(lines)

    @staticmethod
    def _message_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = {
            "content_type": "text",
            "visibility": "normal",
            "tool_name": None,
            "tool_call_id": None,
            "skill_name": None,
            "source": None,
            "version": 1,
        }
        meta.update(extra or {})
        return meta

    @staticmethod
    def _compact_event_data(event: AgentEvent) -> dict[str, Any]:
        data = dict(event.data)
        value = data.get("data")
        if isinstance(value, str) and len(value) > 1000:
            data["data"] = value[:1000]
            data["truncated"] = True
        return data

    @staticmethod
    def _new_run_id() -> str:
        return str(uuid.uuid4())
