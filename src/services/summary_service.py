"""Best-effort rolling summary for complete PydanticAI runs."""

from __future__ import annotations

import logging

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    UserPromptPart,
)

from agent.model_client import get_compress_agent
from core.config import settings
from db.session import AsyncSessionLocal
from repositories.chat import ChatRepository, flatten_batches
from utils.cache import cache_manager
from utils.summary_lock import SummaryLock

logger = logging.getLogger(__name__)


def build_summary_messages(summary_text: str) -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "以下是较早对话的可信滚动摘要，仅作为后续回答背景：\n"
                        f"{summary_text}"
                    )
                )
            ]
        )
    ]


class SummaryService:
    async def try_summarize(self, session_id: str) -> None:
        redis = cache_manager.redis
        if redis is None:
            return
        lock = SummaryLock(redis, ttl_seconds=settings.SUMMARY_LOCK_TTL_SECONDS)
        token = await lock.try_acquire(session_id)
        if token is None:
            return
        try:
            await self._summarize_if_needed(session_id)
        except Exception:
            logger.exception("Chat summary failed for session %s", session_id)
        finally:
            try:
                await lock.release(session_id, token)
            except Exception:
                logger.exception("Failed to release summary lock for %s", session_id)

    async def _summarize_if_needed(self, session_id: str) -> None:
        async with AsyncSessionLocal() as db:
            context = await ChatRepository(db).load_summary_context(session_id)
        if context is None:
            return
        batches = context.unsummarized_batches
        if len(batches) < settings.SUMMARY_TRIGGER_RUNS:
            return
        old_batches = batches[: -settings.KEEP_RECENT_RUNS]
        if not old_batches:
            return

        result = await get_compress_agent().run(
            (
                "请根据以上历史更新滚动摘要，只输出新摘要。保留用户事实、偏好、"
                "决定、约束和未完成事项；删除寒暄与重复内容，不得添加不存在的"
                f"信息。输出不超过 {settings.MAX_SUMMARY_CHARS} 个字符。"
            ),
            message_history=[
                *context.previous_summary,
                *flatten_batches(old_batches),
            ],
        )
        summary_text = str(result.output).strip()
        if not summary_text:
            return
        summary_text = summary_text[: settings.MAX_SUMMARY_CHARS]
        async with AsyncSessionLocal() as db:
            await ChatRepository(db).save_summary(
                session_id=session_id,
                summary_messages=build_summary_messages(summary_text),
                old_cursor=context.old_cursor,
                new_cursor=old_batches[-1].message_id,
            )
