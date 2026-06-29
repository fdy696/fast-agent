"""Agent business orchestration.

Owns DB lifecycle, conversation/message/run state, and SSE mapping.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui.core.events import RunErrorEvent
from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.ui.ag_ui import AGUIAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import get_agent
from agent.prompts import default_system_prompt
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

    @classmethod
    def _normalize_ag_ui_body(cls, body_json: dict[str, Any], user_id: int) -> dict[str, Any]:
        """Build the AG-UI run shape from a simple frontend chat payload."""
        body = dict(body_json)

        def first_value(*keys: str) -> Any:
            for key in keys:
                value = body.get(key)
                if value is not None:
                    return value
            return None

        message = first_value("message", "content")
        if "messages" not in body and message is not None:
            body["messages"] = [{"role": "user", "content": str(message)}]

        body["threadId"] = (
            first_value("threadId", "thread_id", "sessionId", "session_id")
            or str(uuid.uuid4())
        )
        body["runId"] = first_value("runId", "run_id") or cls._new_run_id()
        body["state"] = first_value("state") or {}
        body["tools"] = first_value("tools") or []
        body["context"] = first_value("context") or []
        body["forwardedProps"] = first_value("forwardedProps", "forwarded_props") or {}

        for msg in body.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            msg.setdefault("id", str(uuid.uuid4()))
            user = msg.get("user")
            if not isinstance(user, dict):
                user = {}
                msg["user"] = user
            user.setdefault("id", str(user_id))

        return body

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
                history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
            elif role == "assistant":
                history.append(ModelResponse(parts=[TextPart(content=content)]))
            # tool messages are omitted — pydantic-ai manages tool state internally
        return history


    # ── streaming (AG-UI) path ──────────────────────────────────────────

    async def stream_ag_ui(self, request: Request, user_id: int):
        """AG-UI 协议流式对话 — 使用 pydantic-ai 原生适配器。

        用 from_request() + run_stream() + streaming_response() 三步手动拆解，
        替代便捷方法 dispatch_request()，目的是能够捕获流式传输中途的异常，
        并调用 mark_failed() 将 run 标记为失败。
        """
        perf_start = time.perf_counter()
        logger.info("agent_stream_timing service=agent stage=start elapsed_ms=0")
        # ① 解析请求体，补齐 RunAgentInput 必填字段的默认值
        body = await request.body()
        logger.info(
            "agent_stream_timing service=agent stage=body_read elapsed_ms=%.2f",
            (time.perf_counter() - perf_start) * 1000,
        )
        body_json = self._normalize_ag_ui_body(json.loads(body), user_id)
        run_input = RunAgentInput.model_validate(body_json)
        session_id = run_input.thread_id or str(uuid.uuid4())
        run_id = run_input.run_id
        logger.info(
            "agent_stream_timing service=agent stage=input_validated session_id=%s run_id=%s elapsed_ms=%.2f",
            session_id,
            run_id,
            (time.perf_counter() - perf_start) * 1000,
        )

        # ② 提取用户输入文本
        user_content = ""
        for msg in run_input.messages:
            if msg.role == "user":
                if isinstance(msg.content, str):
                    user_content = msg.content
                elif isinstance(msg.content, list):
                    for item in msg.content:
                        user_content += getattr(item, "text", "") or getattr(item, "content", "") or ""

        # ③ 流前 DB 写入：创建/获取会话 + 创建 run 记录
        conv, _ = await self.conv_repo.get_or_create(
            session_id=session_id, user_id=user_id,
        )
        logger.info(
            "agent_stream_timing service=agent stage=conversation_ready conversation_id=%s elapsed_ms=%.2f",
            conv.id,
            (time.perf_counter() - perf_start) * 1000,
        )
        run = await self.run_repo.create(
            run_id=run_id, conversation_id=conv.id,
            user_id=user_id, model=settings.AGENT_MODEL,
        )
        logger.info(
            "agent_stream_timing service=agent stage=run_created run_pk=%s elapsed_ms=%.2f",
            run.id,
            (time.perf_counter() - perf_start) * 1000,
        )

        # ④ 先查询历史消息（此时 DB 中还没有当前这条，避免重复）
        db_messages = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, limit=20,
        )
        message_history = self._build_message_history(db_messages)
        logger.info(
            "agent_stream_timing service=agent stage=history_loaded count=%s elapsed_ms=%.2f",
            len(message_history),
            (time.perf_counter() - perf_start) * 1000,
        )

        # ⑤ 然后将当前用户消息写入 DB
        await self.msg_repo.insert(
            conversation_id=conv.id, user_id=user_id,
            role="user", content=user_content, run_id=run_id,
            meta=self._message_meta(),
        )
        logger.info(
            "agent_stream_timing service=agent stage=user_message_inserted elapsed_ms=%.2f",
            (time.perf_counter() - perf_start) * 1000,
        )

        system_prompt = default_system_prompt()

        # ⑤ 构建 Agent 并注册工具（build_agent 在校验不到 API Key 时会抛出异常）
        try:
            agent = get_agent()
            logger.info(
                "agent_stream_timing service=agent stage=agent_ready elapsed_ms=%.2f",
                (time.perf_counter() - perf_start) * 1000,
            )
        except Exception as exc:
            await self.run_repo.mark_failed(run, error=str(exc))

            async def error_stream():
                yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': str(exc), 'code': 'model_not_configured'})}\n\n"

            return StreamingResponse(error_stream(), media_type="text/event-stream")

        # ⑥ 构建历史消息列表（将 DB 中的对话转为 pydantic-ai 原生 ModelMessage 对象）
        # AGUIAdapter 内部会将前端提交的 messages 与这里的 message_history 合并；
        # system_prompt 保持干净，不再把历史文本硬塞进去。
        db_messages = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, limit=20,
        )
        message_history = self._build_message_history(db_messages)
        logger.info(
            "agent_stream_timing service=agent stage=history_reloaded count=%s elapsed_ms=%.2f",
            len(message_history),
            (time.perf_counter() - perf_start) * 1000,
        )

        # ⑦ 流完成回调：写入 assistant 消息 + 标记 run 成功
        async def on_complete(agent_result):
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

        # ⑧ 构建 AG-UI 适配器（不再经过 AGUIAdapter 的 RunAgentInput 校验，
        #     直接构造适配器实例，传入已解析的 run_input 参数）
        adapter = AGUIAdapter(
            agent=agent,
            run_input=run_input,
            manage_system_prompt="client",
        )
        logger.info(
            "agent_stream_timing service=agent stage=adapter_created elapsed_ms=%.2f",
            (time.perf_counter() - perf_start) * 1000,
        )

        # ⑨ 流式事件生成器：逐事件 yield AG-UI 事件，中途异常统一捕获
        async def event_stream():
            stream_start = time.perf_counter()
            first_event = True
            logger.info(
                "agent_stream_timing service=agent stage=event_stream_enter elapsed_ms=%.2f",
                (time.perf_counter() - perf_start) * 1000,
            )
            try:
                async for event in adapter.run_stream(
                    message_history=message_history,
                    conversation_id=session_id,
                    instructions=system_prompt,
                    on_complete=on_complete,
                ):
                    if first_event:
                        first_event = False
                        logger.info(
                            "agent_stream_timing service=agent stage=first_event event_type=%s since_request_ms=%.2f since_stream_ms=%.2f",
                            getattr(event, "type", type(event).__name__),
                            (time.perf_counter() - perf_start) * 1000,
                            (time.perf_counter() - stream_start) * 1000,
                        )
                    yield event
            except Exception as exc:
                logger.exception("Agent stream failed for conversation %s", session_id)
                await self.run_repo.mark_failed(run, error=str(exc))
                yield RunErrorEvent(message=str(exc), code="stream_error")

        logger.info(
            "agent_stream_timing service=agent stage=return_streaming_response elapsed_ms=%.2f",
            (time.perf_counter() - perf_start) * 1000,
        )
        # ⑩ 返回 StreamingResponse，流结束后自动关闭连接
        return adapter.streaming_response(event_stream())
    

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
        # ① 查历史（在写入当前消息之前，避免当前消息被当作"历史"重复发送给 LLM）
        db_messages = await self.msg_repo.list_by_conversation(
            conversation_id=conv.id, limit=20,
        )
        message_history = self._build_message_history(db_messages)

        # ② 写入当前用户消息
        await self.msg_repo.insert(
            conversation_id=conv.id, user_id=user_id,
            role="user", content=payload.message, run_id=run_id,
            meta=self._message_meta(payload.metadata),
        )

        system_prompt = payload.system_prompt or default_system_prompt()

        agent = get_agent()

        try:
            result = await agent.run(
                payload.message,
                message_history=message_history,
                instructions=system_prompt,
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
