"""First-principles Agent orchestration with durable native history."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui.core.events import RunErrorEvent
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.ui.ag_ui import AGUIAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import get_agent
from agent.prompts import default_system_prompt
from core.config import settings
from log import logger
from models.conversation import Conversation
from models.conversation_message import ConversationMessage
from repositories import ConversationMessageRepository, ConversationRepository
from schemas.agent import ChatRequest
from services.history_manager import HistoryManager, serialize_native_messages


class AgentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.conv_repo = ConversationRepository(db)
        self.msg_repo = ConversationMessageRepository(db)
        self.history = HistoryManager(self.msg_repo, self.conv_repo)

    @staticmethod
    def _message_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {"content_type": "text", "visibility": "normal"}
        meta.update(extra or {})
        return meta

    @classmethod
    def _normalize_ag_ui_body(
        cls, body: dict[str, Any], user_id: int
    ) -> dict[str, Any]:
        body = dict(body)

        def first(*keys: str) -> Any:
            return next((body[key] for key in keys if body.get(key) is not None), None)

        message = first("message", "content")
        if "messages" not in body and message is not None:
            body["messages"] = [{"role": "user", "content": str(message)}]
        else:
            # The server owns history. Never trust or replay client-supplied history;
            # retain only the newest user message as the current run input.
            latest_user = next(
                (
                    item
                    for item in reversed(body.get("messages") or [])
                    if isinstance(item, dict) and item.get("role") == "user"
                ),
                None,
            )
            body["messages"] = [latest_user] if latest_user is not None else []
        body["threadId"] = first(
            "threadId", "thread_id", "sessionId", "session_id"
        ) or str(uuid.uuid4())
        body["runId"] = first("runId", "run_id") or str(uuid.uuid4())
        body["state"] = first("state") or {}
        body["tools"] = first("tools") or []
        body["context"] = first("context") or []
        body["forwardedProps"] = first("forwardedProps", "forwarded_props") or {}
        for message_item in body.get("messages") or []:
            if not isinstance(message_item, dict):
                continue
            message_item.setdefault("id", str(uuid.uuid4()))
            user = message_item.setdefault("user", {})
            if isinstance(user, dict):
                user.setdefault("id", str(user_id))
        return body

    @staticmethod
    def _extract_user_content(run_input: RunAgentInput) -> str:
        for message in reversed(run_input.messages):
            if message.role != "user":
                continue
            if isinstance(message.content, str):
                content = message.content.strip()
            else:
                content = "".join(
                    getattr(item, "text", "") or getattr(item, "content", "") or ""
                    for item in message.content
                ).strip()
            if content:
                return content
        raise ValueError("user message is empty")

    async def _persist_success(
        self,
        pending: ConversationMessage,
        result: Any,
    ) -> ConversationMessage:
        native = serialize_native_messages(result.new_messages())
        final_message = await self.msg_repo.complete_turn(
            pending_id=pending.id,
            native_messages=native,
        )
        conversation = await self.conv_repo.get_by_id(pending.conversation_id)
        if conversation is not None:
            await self.conv_repo.touch(conversation)
        return final_message

    async def _stream_pending(
        self,
        *,
        run_input: RunAgentInput,
        conversation: Conversation,
        pending: ConversationMessage,
    ) -> StreamingResponse:
        try:
            agent = get_agent()
            message_history = await self.history.build(
                conversation=conversation,
                before_message_id=pending.id,
            )
        except Exception as exc:
            error_message = str(exc)
            await self.msg_repo.mark_failed(pending.id, error_message)

            async def startup_error():
                payload = {
                    "type": "RUN_ERROR",
                    "message": error_message,
                    "code": "agent_start_failed",
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            return StreamingResponse(startup_error(), media_type="text/event-stream")

        async def on_complete(result: Any) -> None:
            await self._persist_success(pending, result)

        adapter = AGUIAdapter(
            agent=agent,
            run_input=run_input,
            manage_system_prompt="client",
        )

        async def events():
            try:
                async for event in adapter.run_stream(
                    message_history=message_history,
                    conversation_id=conversation.session_id,
                    instructions=default_system_prompt(),
                    on_complete=on_complete,
                ):
                    yield event
            except Exception as exc:
                logger.exception(
                    "agent stream failed session_id=%s", conversation.session_id
                )
                await self.msg_repo.mark_failed(pending.id, str(exc))
                yield RunErrorEvent(message=str(exc), code="stream_error")

        return adapter.streaming_response(events())

    async def stream_ag_ui(self, request: Request, user_id: int) -> StreamingResponse:
        body = self._normalize_ag_ui_body(json.loads(await request.body()), user_id)
        run_input = RunAgentInput.model_validate(body)
        session_id = run_input.thread_id or str(uuid.uuid4())
        content = self._extract_user_content(run_input)
        conversation, _ = await self.conv_repo.get_or_create(
            session_id=session_id,
            user_id=user_id,
        )
        pending = await self.msg_repo.create_pending(
            conversation_id=conversation.id,
            user_id=user_id,
            content=content,
            meta=self._message_meta(),
        )
        pending = await self.msg_repo.claim(pending.id)
        return await self._stream_pending(
            run_input=run_input,
            conversation=conversation,
            pending=pending,
        )

    async def retry_stream(self, message_id: int, user_id: int) -> StreamingResponse:
        message = await self.msg_repo.get_by_id(message_id)
        if message is None or message.user_id != user_id:
            raise LookupError("message not found")
        conversation = await self.conv_repo.get_by_id(message.conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise LookupError("conversation not found")
        pending = await self.msg_repo.claim(message_id, retry=True)
        body = self._normalize_ag_ui_body(
            {"message": pending.content, "threadId": conversation.session_id},
            user_id,
        )
        return await self._stream_pending(
            run_input=RunAgentInput.model_validate(body),
            conversation=conversation,
            pending=pending,
        )

    async def run(self, payload: ChatRequest, user_id: int) -> dict[str, Any]:
        session_id = payload.session_id or str(uuid.uuid4())
        conversation, _ = await self.conv_repo.get_or_create(
            session_id=session_id,
            user_id=user_id,
        )
        pending = await self.msg_repo.create_pending(
            conversation_id=conversation.id,
            user_id=user_id,
            content=payload.message,
            meta=self._message_meta(payload.metadata),
        )
        pending = await self.msg_repo.claim(pending.id)
        try:
            history = await self.history.build(
                conversation=conversation,
                before_message_id=pending.id,
            )
            result = await get_agent().run(
                payload.message,
                message_history=history,
                conversation_id=session_id,
                instructions=payload.system_prompt or default_system_prompt(),
            )
            await self._persist_success(pending, result)
        except Exception as exc:
            await self.msg_repo.mark_failed(pending.id, str(exc))
            raise

        tool_calls = [
            {"name": part.tool_name, "args": part.args}
            for message in result.new_messages()
            for part in message.parts
            if isinstance(part, ToolCallPart)
        ]
        return {
            "answer": str(result.output or ""),
            "session_id": session_id,
            "model": settings.AGENT_MODEL,
            "tool_calls": tool_calls,
        }
