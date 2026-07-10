"""Chat service — message lifecycle, history building, and summary compaction."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui.core.events import RunErrorEvent
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import call_compress_model, get_agent
from agent.prompts import default_system_prompt
from core.config import settings
from log import logger
from repositories.chat_message import ChatMessageRepository
from repositories.chat_session import ChatSessionRepository

SUMMARY_TRIGGER_TURNS = settings.SUMMARY_TRIGGER_TURNS
SUMMARY_TRIGGER_ESTIMATED_TOKENS = settings.SUMMARY_TRIGGER_ESTIMATED_TOKENS
KEEP_RECENT_TURNS = settings.KEEP_RECENT_TURNS
MAX_SUMMARY_CHARS = settings.MAX_SUMMARY_CHARS


def _is_usage_limit(exc: Exception) -> bool:
    if isinstance(exc, UsageLimitExceeded):
        return True
    cause = getattr(exc, "__cause__", None)
    while cause is not None:
        if isinstance(cause, UsageLimitExceeded):
            return True
        cause = getattr(cause, "__cause__", None)
    return False


def _infer_role(msg_data: dict) -> str:
    parts = msg_data.get("parts", [])
    if not parts:
        return "system"
    first = parts[0]
    kind = first.get("part_kind", "")
    if kind == "user-prompt":
        return "user"
    if kind in ("tool-call", "tool-return"):
        return "tool"
    if kind == "system-prompt":
        return "system"
    if kind == "text":
        role = msg_data.get("role", "")
        return "assistant" if role == "model-response" else "system"
    return "system"


def _extract_content(msg_data: dict) -> str:
    parts = msg_data.get("parts", [])
    texts = []
    for p in parts:
        kind = p.get("part_kind", "")
        if kind in ("text", "user-prompt"):
            texts.append(p.get("content", "") or p.get("text", "") or "")
        elif kind == "tool-call":
            name = p.get("tool_name", "")
            args = p.get("args", {})
            texts.append(f"[tool:{name}]({str(args)[:200]})")
        elif kind == "tool-return":
            texts.append("[tool result]")
    return "\n".join(t for t in texts if t)


def text_projection(messages: list[ModelMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        for p in m.parts:
            if isinstance(p, UserPromptPart):
                lines.append(f"用户：{p.content}")
            elif isinstance(p, TextPart) and isinstance(m, ModelResponse):
                lines.append(f"助手：{p.content}")
            elif isinstance(p, ToolReturnPart):
                content = str(p.content)
                if isinstance(content, str) and len(content) > 200:
                    content = content[:200] + "..."
                lines.append(f"工具结果：{content}")
    return "\n".join(lines)


def summarization_prompt() -> str:
    return (
        "将旧对话压缩为后续对话所需的上下文。\n\n"
        "必须保留：\n"
        "1. 用户明确提供的事实和偏好；\n"
        "2. 已确认的决定与结论；\n"
        "3. 当前目标和重要约束；\n"
        "4. 尚未完成的事项；\n"
        "5. 后续可能引用的重要工具结果；\n"
        "6. 对旧摘要的有效修正。\n\n"
        "删除寒暄、重复内容、无关信息、工具协议细节和已被推翻的信息。\n"
        "冲突时以较新的事实为准。\n"
        "不得添加对话中不存在的信息。\n"
        f"输出不超过{MAX_SUMMARY_CHARS}个中文字符。"
    )


class ChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.session_repo = ChatSessionRepository(db)
        self.msg_repo = ChatMessageRepository(db)

    @staticmethod
    def _new_turn_id() -> str:
        return str(uuid.uuid4())

    async def create_pending_turn(
        self, *, session_id: str, user_id: int, content: str
    ) -> dict:
        session = await self.session_repo.get_by_session_id(
            session_id=session_id, user_id=user_id
        )
        if not session:
            raise HTTPException(404, "Session not found")
        cancelled = await self.msg_repo.cancel_running_messages(
            session_id=session_id
        )
        if cancelled:
            logger.info("Cancelled %d messages in session %s", cancelled, session_id)
        try:
            from utils.cache import cache_manager
            await cache_manager._client.delete(f"lock:chat:{session_id}")
        except Exception:
            pass
        turn_id = self._new_turn_id()
        msg = await self.msg_repo.insert(
            session_id=session_id, turn_id=turn_id,
            message_index=0, status="pending", role="user", content=content,
        )
        return {
            "id": msg.id, "turn_id": msg.turn_id,
            "session_id": msg.session_id, "status": msg.status,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }

    async def execute_turn(
        self, message_id: int, user_id: int
    ) -> StreamingResponse:
        msg = await self.msg_repo.get_by_id_for_update(message_id)
        if not msg:
            raise HTTPException(404, "Message not found")
        session = await self.session_repo.get_by_session_id(
            session_id=msg.session_id, user_id=user_id
        )
        if not session:
            raise HTTPException(403, "Access denied")
        if msg.status not in ("pending", "failed"):
            raise HTTPException(409, f"Cannot execute message in status {msg.status}")
        try:
            from utils.cache import cache_manager
            redis = cache_manager._client
        except Exception:
            redis = None
        lock_key = f"lock:chat:{msg.session_id}"
        if redis:
            acquired = await redis.set(lock_key, "1", nx=True, ex=120)
            if not acquired:
                raise HTTPException(423, "Session is busy, please retry")
        try:
            retry_count = msg.retry_count
            if msg.status == "failed":
                retry_count += 1
            await self.msg_repo.update_status(msg, "running")
            msg.retry_count = retry_count
            history = await build_model_history(
                self.db, session_id=msg.session_id,
                before_message_id=msg.id,
            )
            agent = get_agent()
            system_prompt = default_system_prompt()
            run_input = RunAgentInput(
                thread_id=msg.session_id, run_id=msg.turn_id,
                messages=[{"role": "user", "content": msg.content or ""}],
                tools=[], context=[], forwardedProps={}, state={},
            )
            adapter = AGUIAdapter(
                agent=agent, run_input=run_input, manage_system_prompt="client",
            )

            async def on_complete(agent_result):
                await self._commit_turn(message_id, agent_result, session)

            continue_count = 0

            async def event_stream():
                nonlocal continue_count
                event_count = 0
                while True:
                    try:
                        async for event in adapter.run_stream(
                            message_history=history,
                            conversation_id=msg.session_id,
                            instructions=system_prompt,
                            on_complete=on_complete if continue_count == 0 else None,
                        ):
                            event_count += 1
                            if event_count % 10 == 0:
                                from db.session import AsyncSessionLocal
                                async with AsyncSessionLocal() as check_db:
                                    check_repo = ChatMessageRepository(check_db)
                                    current = await check_repo.get_by_id(msg.id)
                                    if current and current.status == "cancelled":
                                        logger.info("Turn %s cancelled mid-stream", msg.turn_id)
                                        raise asyncio.CancelledError()
                            yield event
                        break
                    except Exception as exc:
                        if _is_usage_limit(exc) and continue_count < 2:
                            continue_count += 1
                            logger.info("Token limit reached, auto-continuing (%d/2)", continue_count)
                            continue
                        if isinstance(exc, asyncio.CancelledError):
                            await self.msg_repo.update_status(msg, "cancelled")
                            yield RunErrorEvent(message="Cancelled", code="cancelled")
                            break
                        logger.exception("Agent stream failed for %s", msg.session_id)
                        await self.msg_repo.update_status(msg, "failed", error_message=str(exc)[:500])
                        yield RunErrorEvent(message=str(exc), code="stream_error")
                        break

            return adapter.streaming_response(event_stream())
        finally:
            if redis:
                await redis.delete(lock_key)

    async def _commit_turn(self, message_id: int, agent_result, session) -> None:
        from datetime import datetime, timezone
        from models.chat_message import ChatMessage as ChatMessageModel
        new_messages = agent_result.new_messages()
        serialized = ModelMessagesTypeAdapter.dump_python(new_messages, mode="json")
        pending = await self.msg_repo.get_by_id(message_id)
        if not pending or pending.status == "cancelled":
            return
        now = datetime.now(timezone.utc)
        pending.message_data = serialized[0]
        pending.status = "completed"
        pending.updated_at = now
        for i, msg_data in enumerate(serialized[1:], start=1):
            role = _infer_role(msg_data)
            content = _extract_content(msg_data)
            self.db.add(ChatMessageModel(
                session_id=pending.session_id, turn_id=pending.turn_id,
                message_index=i, status="completed", role=role,
                content=content, message_data=msg_data,
            ))
        await self.db.commit()
        await self.session_repo.touch(session)
        try:
            from utils.queue import queue
            await queue.enqueue("generate_title", session_id=session.session_id, first_msg=pending.content)
        except Exception:
            pass

    async def retry_turn(self, message_id: int, user_id: int) -> StreamingResponse:
        msg = await self.msg_repo.get_by_id(message_id)
        if not msg:
            raise HTTPException(404, "Message not found")
        if msg.status != "failed":
            raise HTTPException(409, f"Cannot retry message in status {msg.status}")
        return await self.execute_turn(message_id, user_id)

    async def list_messages(
        self, *, session_id: str, user_id: int, after_id: int | None = None
    ) -> list[dict]:
        session = await self.session_repo.get_by_session_id(
            session_id=session_id, user_id=user_id
        )
        if not session:
            return []
        msgs = await self.msg_repo.list_by_session(
            session_id=session_id, after_id=after_id
        )
        return [
            {
                "id": m.id, "turn_id": m.turn_id,
                "message_index": m.message_index, "status": m.status,
                "role": m.role, "content": m.content,
                "error_message": m.error_message,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ]


async def build_model_history(
    db: AsyncSession, *, session_id: str, before_message_id: int,
) -> list[ModelMessage]:
    session_repo = ChatSessionRepository(db)
    msg_repo = ChatMessageRepository(db)
    session = await session_repo.get_by_session_id(
        session_id=session_id, user_id=0
    )
    if not session:
        return []
    messages = await msg_repo.list_completed_turns(
        session_id=session_id,
        after_message_id=session.summarized_through_message_id,
        before_message_id=before_message_id,
    )
    turns_map: dict[str, list] = defaultdict(list)
    for m in messages:
        turns_map[m.turn_id].append(m)
    turns = list(turns_map.values())
    turns.sort(key=lambda t: t[0].id)
    total_chars = sum(
        len(m.content or "") + len(str(m.message_data or {}))
        for t in turns for m in t
    )
    estimated_tokens = max(1, total_chars // 3)
    need = (
        len(turns) >= SUMMARY_TRIGGER_TURNS
        or estimated_tokens >= SUMMARY_TRIGGER_ESTIMATED_TOKENS
    )
    if need:
        try:
            await compact_history(db, session, turns)
            session = await session_repo.get_by_session_id(
                session_id=session_id, user_id=0
            )
            if session:
                messages = await msg_repo.list_completed_turns(
                    session_id=session_id,
                    after_message_id=session.summarized_through_message_id,
                    before_message_id=before_message_id,
                )
                turns_map = defaultdict(list)
                for m in messages:
                    turns_map[m.turn_id].append(m)
                turns = list(turns_map.values())
                turns.sort(key=lambda t: t[0].id)
        except Exception:
            logger.exception("Summary compaction failed, using recent window")
    recent_turns = turns[-KEEP_RECENT_TURNS:] if len(turns) > KEEP_RECENT_TURNS else turns
    result: list[ModelMessage] = []
    if session and session.history_summary:
        result.append(
            ModelRequest(parts=[
                SystemPromptPart(
                    content=f"较早对话摘要，仅作为历史上下文：\n{session.history_summary}"
                )
            ])
        )
    for turn in recent_turns:
        for m in turn:
            if m.message_data:
                try:
                    native = ModelMessagesTypeAdapter.validate_python(m.message_data)
                    if isinstance(native, list):
                        result.extend(native)
                    else:
                        result.append(native)
                except Exception:
                    logger.warning("Failed to deserialize message %d", m.id)
    return result


async def compact_history(db: AsyncSession, session, turns: list[list]) -> None:
    from datetime import datetime, timezone
    from sqlalchemy import update
    from models.chat_session import ChatSession
    from openai.types.chat import ChatCompletionMessageParam
    session_repo = ChatSessionRepository(db)
    old_cursor = session.summarized_through_message_id
    if len(turns) <= KEEP_RECENT_TURNS:
        return
    compact_turns = turns[:-KEEP_RECENT_TURNS]
    native_msgs: list[ModelMessage] = []
    for turn in compact_turns:
        for m in turn:
            if m.message_data:
                try:
                    msg = ModelMessagesTypeAdapter.validate_python(m.message_data)
                    if isinstance(msg, list):
                        native_msgs.extend(msg)
                    else:
                        native_msgs.append(msg)
                except Exception:
                    pass
    old_summary = session.history_summary or ""
    projection = text_projection(native_msgs)
    compress_messages: list[dict] = []
    if old_summary:
        compress_messages.append({"role": "system", "content": "以下是较早对话的摘要，请在新摘要中保留其中的关键信息。"})
        compress_messages.append({"role": "user", "content": f"旧摘要：\n{old_summary}\n\n新对话：\n{projection}"})
    else:
        compress_messages.append({"role": "system", "content": summarization_prompt()})
        compress_messages.append({"role": "user", "content": projection})
    try:
        result = await call_compress_model(compress_messages)
        new_summary = result.choices[0].message.content or ""
        new_summary = new_summary[:MAX_SUMMARY_CHARS * 2]
    except Exception:
        logger.exception("Summary model call failed")
        return
    if not new_summary.strip():
        return
    last_compact_turn = compact_turns[-1]
    new_cursor = last_compact_turn[-1].id
    now = datetime.now(timezone.utc)
    update_result = await db.execute(
        update(ChatSession)
        .where(
            ChatSession.session_id == session.session_id,
            ChatSession.summarized_through_message_id == old_cursor,
        )
        .values(
            history_summary=new_summary,
            summarized_through_message_id=new_cursor,
            summary_updated_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    if update_result.rowcount == 0:
        logger.info("Summary CAS: no rows updated, another worker beat us")
    else:
        logger.info("Summary CAS: compacted %d turns, new cursor=%d", len(compact_turns), new_cursor)
