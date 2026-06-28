"""Agent business orchestration.

Owns DB lifecycle, conversation/message/run state, and SSE mapping.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from ag_ui.core import RunAgentInput
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.ui.ag_ui import AGUIAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import build_agent
from agent.prompts import default_system_prompt
from agent.tools import ask_human, current_time
from core.config import settings
from log import logger
from repositories import (
    AgentRunRepository,
    ConversationMessageRepository,
    ConversationRepository,
)
from schemas.agent import ChatRequest


class AgentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.conv_repo = ConversationRepository(db)
        self.msg_repo = ConversationMessageRepository(db)
        self.run_repo = AgentRunRepository(db)

    # ── helpers (plain functions — no self needed) ──────────────────────

    @staticmethod
    def _message_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "content_type": "text", "visibility": "normal",
            "tool_name": None, "tool_call_id": None,
            "skill_name": None, "source": None, "version": 1,
        }
        meta.update(extra or {})
        return meta

    @staticmethod
    def _new_run_id() -> str:
        return str(uuid.uuid4())

    # ── message history builder ─────────────────────────────────────────

    def _build_message_history(
        self, messages: list[Any],
    ) -> list[ModelMessage]:
        """Convert DB conversation messages into PydanticAI ModelMessage objects.

        Uses the native pydantic-ai types to represent conversation history,
        so the LLM sees proper role attribution instead of text mashed into
        the system prompt.
        """
        history: list[ModelMessage] = []
        for m in messages:
            role = getattr(m, "role", None)
            content = getattr(m, "content", "")
            if not content:
                continue
            if role == "user":
                history.append(ModelRequest(parts=[TextPart(content=content)]))
            elif role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=content)]))
            # tool messages are omitted — pydantic-ai manages tool state internally
        return history

    # ── non-streaming path ──────────────────────────────────────────────

    async def run(self, payload: ChatRequest, user_id: int) -> dict[str, Any]:
        """Non-streaming chat using pydantic-aiʼs native agent.run()."""
        if payload.session_id is None:
            payload.session_id = str(uuid.uuid4())

        session_id = payload.session_id
        run_id = self._new_run_id()

        conv, _ = await self.conv_repo.get_or_create(
            session_id=session_id, user_id=user_id,
        )
        run = await self.run_repo.create(
            run_id=run_id, conversation_id=conv.id,
            user_id=user_id, model=settings.AGENT_MODEL,
        )
        await self.msg_repo.insert(
            conversation_id=conv.id, user_id=user_id,
            role="user", content=payload.message, run_id=run_id,
            meta=self._message_meta(payload.metadata),
        )

        # Build message history from prior conversation — pydantic-ai
        # receives proper ModelMessage objects with role attribution.
        db_messages = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, limit=20,
        )
        message_history = self._build_message_history(db_messages)

        system_prompt = payload.system_prompt or default_system_prompt()

        agent = build_agent(system_prompt=system_prompt)
        agent.tool(current_time)
        agent.tool(ask_human)

        try:
            result = await agent.run(
                payload.message,
                message_history=message_history,
            )
        except Exception as exc:
            await self.run_repo.mark_failed(run, error=str(exc))
            raise

        answer = str(result.output) if result.output is not None else ""

        # Extract tool calls from parts within new messages
        tool_calls: list[dict[str, Any]] = []
        for msg in result.new_messages():
            for part in getattr(msg, "parts", []):
                if getattr(part, "part_kind", None) == "tool-call":
                    tool_calls.append({
                        "name": part.tool_name,
                        "args": part.args,
                    })

        assistant_msg = await self.msg_repo.insert(
            conversation_id=conv.id, user_id=user_id,
            role="assistant", content=answer, run_id=run_id,
            meta=self._message_meta({"content_type": "markdown"}),
        )
        await self.run_repo.mark_succeeded(run, assistant_message_id=assistant_msg.id)
        await self.conv_repo.touch(conv)

        return {
            "answer": answer,
            "session_id": session_id,
            "model": settings.AGENT_MODEL,
            "tool_calls": tool_calls,
        }

    # ── streaming (AG-UI) path ──────────────────────────────────────────

    async def stream_ag_ui(self, request: Request, user_id: int):
        """AG-UI protocol streaming via pydantic-ai.

        Uses from_request() + run_stream() + streaming_response() separately
        (instead of the convenience dispatch_request()) so that stream-level
        errors can be caught and the run marked as failed.
        """
        body = await request.body()
        run_input = RunAgentInput.model_validate_json(body)
        session_id = run_input.thread_id or str(uuid.uuid4())
        run_id = self._new_run_id()

        # Extract user message content for DB persistence
        user_content = ""
        for msg in run_input.messages:
            if getattr(msg, "role", None) == "user":
                content_val = getattr(msg, "content", None)
                if isinstance(content_val, str):
                    user_content = content_val
                elif isinstance(content_val, list):
                    for item in content_val:
                        text = (
                            getattr(item, "text", None)
                            or getattr(item, "content", None)
                            or ""
                        )
                        user_content += text

        # ── pre-stream DB writes ──
        conv, _ = await self.conv_repo.get_or_create(
            session_id=session_id, user_id=user_id,
        )
        run = await self.run_repo.create(
            run_id=run_id, conversation_id=conv.id,
            user_id=user_id, model=settings.AGENT_MODEL,
        )
        await self.msg_repo.insert(
            conversation_id=conv.id, user_id=user_id,
            role="user", content=user_content, run_id=run_id,
            meta=self._message_meta(),
        )

        system_prompt = default_system_prompt()

        # Build agent (model_client may raise)
        try:
            agent = build_agent(system_prompt=system_prompt)
            agent.tool(current_time)
            agent.tool(ask_human)
        except Exception as exc:
            await self.run_repo.mark_failed(run, error=str(exc))

            async def error_stream():
                yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': str(exc), 'code': 'model_not_configured'})}\n\n"

            return StreamingResponse(error_stream(), media_type="text/event-stream")

        # DB message history for conversation continuity.
        # AGUIAdapter merges this with frontend-submitted messages; the
        # system prompt stays clean — no history text injected into it.
        db_messages = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, limit=20,
        )
        message_history = self._build_message_history(db_messages)

        async def on_complete(agent_result):
            """Write assistant message + mark run succeeded after stream completes."""
            output = agent_result.output
            if output is None:
                output = ""

            assistant_msg = await self.msg_repo.insert(
                conversation_id=conv.id, user_id=user_id,
                role="assistant", content=str(output), run_id=run_id,
                meta=self._message_meta({"content_type": "markdown"}),
            )
            await self.run_repo.mark_succeeded(run, assistant_message_id=assistant_msg.id)
            await self.conv_repo.touch(conv)

        # Build the adapter and run stream manually so we can wrap the
        # event iterator and catch errors that happen *during* streaming
        # (dispatch_request returns a Response before the stream starts,
        # so its try/except cannot catch mid-stream failures).
        adapter = await AGUIAdapter.from_request(
            request,
            agent=agent,
            manage_system_prompt="client",
        )

        async def event_stream():
            """Wrap run_stream to catch stream-level errors and mark run failed."""
            try:
                async for event in adapter.run_stream(
                    message_history=message_history,
                    conversation_id=session_id,
                    on_complete=on_complete,
                ):
                    yield event
            except Exception as exc:
                logger.exception("Agent stream failed for conversation %s", session_id)
                await self.run_repo.mark_failed(run, error=str(exc))
                yield adapter.encode_event(
                    type="RUN_ERROR",
                    message=str(exc),
                    code="stream_error",
                )

        return adapter.streaming_response(event_stream())
