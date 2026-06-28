from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.model_client import call_compress_model
from core.config import settings
from log import logger
from models.conversation import Conversation
from models.travel_plan import TravelPlan
from utils.cache import cache_manager

# 滚动摘要参数
TAIL_LIMIT = 20          # 原文尾部超过此条数 → 触发推进
CHUNK_SIZE = 10          # 每次推进吃掉的旧消息条数（上限）
MAX_ADVANCES_PER_TURN = 2  # 单轮对话最多推进几次（限制延迟）
SUMMARY_MAX = 500        # 摘要硬截断字数


async def history_convo_list(
    session_id: str, content: str, user_id: int, session: AsyncSession
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, int]:
    """加载对话历史 + 滚动摘要/游标。

    返回 (history_for_model, storage, summary, cursor)：
    - history_for_model：喂模型的尾部 = 摘要(若有) + past[cursor:] 过滤后的文本
    - storage：past + 本轮 user 消息（供 save_conversation 追加最终 assistant 后写回）
    - summary / cursor：当前滚动摘要与游标（供 maybe_advance_summary 推进）
    """
    redis_key = f"chat_history:{user_id}:{session_id}"
    cached = await cache_manager.get(redis_key)
    if cached is not None:
        if isinstance(cached, dict):  # 新形状
            past = cached.get("messages") or []
            summary = cached.get("summary")
            cursor = int(cached.get("cursor") or 0)
        else:  # 旧形状：裸 list（视为无摘要、游标 0）
            past = cached
            summary = None
            cursor = 0
    else:
        stmt = (
            select(Conversation)
            .where(Conversation.session_id == session_id)
            .where(Conversation.user_id == user_id)
        )
        result = await session.execute(stmt)
        db_convo = result.scalar_one_or_none()
        if db_convo:
            past = db_convo.history_messages or []
            summary = db_convo.summary
            cursor = db_convo.summary_cursor or 0
        else:
            past = []
            summary = None
            cursor = 0

    # 喂模型的尾部：摘要前置 + past[cursor:] 经 #2 过滤
    history_for_model: list[dict[str, Any]] = []
    if summary:
        history_for_model.append(
            {"role": "system", "content": f"[历史摘要] {summary}"}
        )
    for msg in past[cursor:]:
        role = msg.get("role")
        if role == "system":
            continue
        # 跳过过往工具脚手架：中间态 assistant(tool_calls) 和 tool 结果，
        # 避免重建出缺失 tool_call_id 的孤立 tool 消息导致网关 400。
        if role == "tool":
            continue
        if role == "assistant" and msg.get("tool_calls"):
            continue
        history_for_model.append({"role": role, "content": msg.get("content") or ""})

    storage = past + [{"role": "user", "content": content}]
    return history_for_model, storage, summary, cursor


async def save_conversation(
    session_id: str,
    user_id: int,
    session: AsyncSession,
    storage: list[dict[str, Any]],
    title: str | None = None,
) -> bool:
    """保存对话到 PG + Redis（双写），Redis 缓存 2 小时。

    title 仅对「新建会话」生效；existing 行不更新标题。
    返回 True=本次为新建会话（调用方可在外决定是否需要补标题）。
    summary / summary_cursor 不在此维护（由 maybe_advance_summary 推进），
    existing 行原样保留，新行用默认 None / 0。
    """
    stmt = (
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .where(Conversation.user_id == user_id)
    )
    result = await session.execute(stmt)
    convo = result.scalar_one_or_none()

    if convo:
        convo.history_messages = storage
        is_new = False
    else:
        convo = Conversation(
            session_id=session_id,
            title=title or "新对话",
            history_messages=storage,
            user_id=user_id,
        )
        session.add(convo)
        is_new = True
    await session.commit()

    await cache_manager.set(
        f"chat_history:{user_id}:{session_id}",
        {"messages": storage, "summary": convo.summary, "cursor": convo.summary_cursor or 0},
        ttl=settings.AGENT_CACHE_TTL,
    )
    return is_new


async def maybe_advance_summary(
    session_id: str,
    user_id: int,
    storage: list[dict[str, Any]],
    summary: str | None,
    cursor: int,
    session: AsyncSession,
) -> tuple[str | None, int]:
    """尾部超长时增量推进滚动摘要，写回 PG + 缓存。返回新的 (summary, cursor)。

    每次吃掉一个对齐到完整轮次边界的 chunk，把「旧摘要 + chunk」合并为新摘要，
    游标前移。合并失败则不动游标（下轮重试）。单轮最多推进 MAX_ADVANCES_PER_TURN 次。
    """
    advances = 0
    while len(storage) - cursor > TAIL_LIMIT and advances < MAX_ADVANCES_PER_TURN:
        chunk_end = _align_turn_boundary(storage, cursor + CHUNK_SIZE, cursor)
        if chunk_end <= cursor:
            break
        chunk = storage[cursor:chunk_end]
        new_summary = await _merge_summary(summary, chunk)
        if new_summary is None:
            break  # 合并失败，cursor 不动
        summary, cursor = new_summary, chunk_end
        advances += 1

    if advances > 0:
        stmt = (
            select(Conversation)
            .where(Conversation.session_id == session_id)
            .where(Conversation.user_id == user_id)
        )
        result = await session.execute(stmt)
        convo = result.scalar_one_or_none()
        if convo:
            convo.summary = summary
            convo.summary_cursor = cursor
            await session.commit()
        await cache_manager.set(
            f"chat_history:{user_id}:{session_id}",
            {"messages": storage, "summary": summary, "cursor": cursor},
            ttl=settings.AGENT_CACHE_TTL,
        )
    return summary, cursor


def _align_turn_boundary(
    storage: list[dict[str, Any]], target: int, cursor: int
) -> int:
    """从 target 往回找最近一个「最终 assistant」(无 tool_calls) 的下一个索引，
    保证 chunk 切在完整轮次边界。找不到则返回 cursor（不切）。"""
    end = min(target, len(storage))
    while end > cursor:
        msg = storage[end - 1]
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            return end
        end -= 1
    return cursor


async def _merge_summary(
    existing: str | None, chunk: list[dict[str, Any]]
) -> str | None:
    """把「已有摘要 + 一个 chunk」合并为新的 ≤200 字摘要。失败返回 None。"""
    lines = [f"{m.get('role')}: {str(m.get('content') or '')[:400]}" for m in chunk]
    prompt = (
        "你是对话历史压缩专家。\n"
        f"【已有摘要】\n{existing or '暂无'}\n\n"
        "【新增对话】\n" + "\n".join(lines) + "\n\n"
        "【任务】将上述信息合并为一段 200 字以内的摘要，保留关键事实、决策、用户偏好；"
        "新信息与旧摘要冲突时以新信息为准；只返回摘要文本，不要加解释。"
    )
    try:
        res = await call_compress_model(
            messages=[{"role": "system", "content": prompt}]
        )
        summary = (res.choices[0].message.content or "").strip()
        return summary[:SUMMARY_MAX] or None
    except Exception as e:
        logger.warning(f"summary merge failed (non-blocking): {e}")
        return None


async def get_user_chat_list(
    user_id: int, session: AsyncSession
) -> list[dict[str, Any]]:
    """获取某个用户的所有会话列表（按更新时间降序）"""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(desc(Conversation.updated_at))
    )
    result = await session.execute(stmt)
    chat_list = [
        {"session_id": row.session_id, "title": row.title}
        for row in result.scalars().all()
    ]
    return chat_list


async def get_chat_detail(
    user_id: int, session_id: str, session: AsyncSession
) -> Any:
    """获取某个会话的完整历史消息"""
    stmt = (
        select(Conversation.history_messages)
        .where(Conversation.user_id == user_id)
        .where(Conversation.session_id == session_id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row if row else []


async def get_travel_plan(
    plan_id: str, session_id: str, user_id: int, session: AsyncSession
) -> Any:
    """查询某次行程计划的 JSON 数据（带归属校验）。

    通过 session_id 联查 Conversation.user_id 必须等于请求者，
    否则视为不归属（返回 None，端点返 404），避免 IDOR 泄露他人行程。
    """
    owner_stmt = select(Conversation.user_id).where(Conversation.session_id == session_id)
    owner = (await session.execute(owner_stmt)).scalar_one_or_none()
    if owner != user_id:
        return None

    stmt = (
        select(TravelPlan.plan_data)
        .where(TravelPlan.plan_id == plan_id)
        .where(TravelPlan.session_id == session_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
