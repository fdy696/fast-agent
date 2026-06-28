"""Agent 主循环 — 加载历史 → 循环调用 LLM → 后处理 → 保存"""

import asyncio
import uuid
from typing import Any, Iterable, List, cast

from openai import AsyncStream
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolUnionParam,
)
from sqlalchemy import select

from agent.hooks import register_hook, trigger_hooks
from agent.cost import calculate, track_daily, daily_summary
from agent.feishu import send_alert
from agent.model_prompt import build_system_prompt
from agent.model_client import call_model
from agent.context_store import (
    history_convo_list,
    maybe_advance_summary,
    save_conversation,
)
from agent.post_process import (
    deepseek_compress_chat,
    generate_conversation_title,
    safe_extract_itinerary,
)
from agent.context_manager import manage_context
from agent.prompt_builder import build_runtime_prompt
from agent.stream_consumer import consume_stream
from agent.tool_executor import execute_tool_calls
from agent.tools import get_all_tool_schemas
from core.config import settings
from db.session import AsyncSessionLocal
from models.conversation import Conversation
from models.token_usage import TokenUsage
from log import logger


_trace = lambda sid, msg: logger.info(f"[trace:{str(sid)[:8]}] {msg}")


async def _save_usage(session_id: str, step: int, usage: dict, cost: float) -> None:
    """fire-and-forget — 不阻塞对话流。"""
    try:
        async with AsyncSessionLocal() as s:
            s.add(TokenUsage(
                session_id=session_id,
                model=settings.AGENT_MODEL,
                prompt_tokens=usage.get("prompt_tokens", 0),
                cached_tokens=usage.get("cached_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cost=cost,
                step=step,
            ))
            await s.commit()
    except Exception:
        pass


def _register_travel_hooks() -> None:
    """模块加载时注册 travel-planner 的硬兜底 hook。"""
    from agent.hooks_travel import activate_skill

    register_hook("PostToolUse", activate_skill)


_register_travel_hooks()


async def _run_agent(
    session_id: str,
    user_id: int,
    content: str,
    queue: asyncio.Queue,
) -> Any:
    """Agent 主循环：加载历史 → 循环调用 LLM → 后处理 → 保存

    不持有外部 DB session —— 在需要时开短 session（加载历史、保存），
    避免在 SSE 流式期间（10–30s）整段占用 PG 连接。
    """
    from agent.skills.registry import registry
    from repositories.user import UserRepository

    # 1. 加载历史 + 用户长期偏好（短 session，用完即闭）
    _trace(session_id, "start")
    async with AsyncSessionLocal() as session:
        history, storage, summary, cursor = await history_convo_list(
            session_id, content, user_id, session
        )
        user_preference = await UserRepository(session).get_preference(user_id)

    # 漏触发检测
    from agent.hooks_travel import _check_skill_gap
    _check_skill_gap()

    # 2. 初始化运行时状态
    active_skills: set[str] = set()
    active_refs: set[tuple[str, str]] = set()
    working_messages: List[dict[str, Any]] = [{"role": "user", "content": content}]
    all_tools = get_all_tool_schemas()
    total_cost = 0.0

    # 3. Agent 循环
    agent_context: List[dict[str, Any]] = []
    for step in range(settings.AGENT_MAX_STEPS):
        system_prompt = build_runtime_prompt(
            base_prompt=build_system_prompt(user_preference=user_preference),
            active_skills=active_skills,
            active_refs=active_refs,
        )

        agent_context = [
            {"role": "system", "content": system_prompt},
            *history,
            *working_messages,
        ]

  

        # 上下文安全网：极端超预算时硬裁剪最旧消息（无 LLM 调用）。
        # 滚动摘要由 maybe_advance_summary 在对话结束后增量推进。
        agent_context = await manage_context(agent_context)
        
        _trace(session_id, f"step=llm_call step={step}")
        stream: AsyncStream[ChatCompletionChunk] = await call_model(
            messages=cast(Iterable[ChatCompletionMessageParam], agent_context),
            tools=cast(Iterable[ChatCompletionToolUnionParam], all_tools),
            response_format="text",
            stream=True,
        )

        full_text, tool_calls, usage = await consume_stream(stream, queue)

        # token 费用 trace + 入库
        if usage:
            cost = calculate(usage, settings.AGENT_MODEL)
            _trace(session_id, f"step=cost cost=${cost:.5f} tokens={usage['prompt_tokens']}/{usage['completion_tokens']}")
            total_cost += cost
            asyncio.create_task(_save_usage(session_id, step, usage, cost))

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_text or None}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        working_messages.append(assistant_msg)

        if not tool_calls:
            break

        # PostToolUse hook — skill 激活
        for tc in tool_calls:
            _trace(session_id, f"step=tool name={tc['function']['name']}")
            trigger_hooks("PostToolUse", tc, active_skills, active_refs, registry)

        tool_messages = await execute_tool_calls(tool_calls)
        working_messages.extend(tool_messages)

    else:
        await queue.put(("error", "工具调用次数过多，已停止继续执行。"))
        working_messages.append({
            "role": "assistant",
            "content": "工具调用次数过多，已停止继续执行。"
        })

    # 4. 后处理
    full_text = working_messages[-1].get("content", "") if working_messages else ""

    _trace(session_id, f"step=done total_steps={step + 1} total_cost=${total_cost:.5f}")

    # 每日费用累加 + 飞书告警
    if total_cost > 0 and track_daily(total_cost, settings.TOKEN_COST_DAILY_LIMIT):
        s = daily_summary()
        asyncio.create_task(
            send_alert("⚠️ Token 费用超限",
                       f"**当日累计**: ${s['cost']:.4f}\n**阈值**: ${settings.TOKEN_COST_DAILY_LIMIT:.2f}\n"
                       f"**本次**: ${total_cost:.5f}\n**会话**: {session_id}"))

    is_plan = "travel-planner" in active_skills

    compressed = (
        await deepseek_compress_chat(full_text) if is_plan
        else full_text
    )

    current_plan_id = str(uuid.uuid4()) if is_plan else None

    new_assistant_msg = {
        "role": "assistant",
        "content": compressed,
        "original": full_text,
        "active_skills": list(active_skills),
    }

    if is_plan and current_plan_id:
        new_assistant_msg["travel_plans"] = {
            "id": current_plan_id,
            "status": "pending",
            "message": "行程清单生成中,点击查看！",
        }

    storage.append(new_assistant_msg)
    # 5.5 标题：仅对新建会话生成，且只基于用户首条消息
    #     （不混提示词/历史/工具结果，短消息直接截断、零 LLM 调用）。
    #     探测会话是否已存在 → 不存在才调模型；先离 session 生成好 title，
    #     再进短 session 写入，避免标题的 LLM 调用期间占用 PG 连接（#6）。
    title: str | None = None
    if content:
        async with AsyncSessionLocal() as session:
            exists = (await session.execute(
                select(Conversation.session_id).where(
                    Conversation.session_id == session_id,
                    Conversation.user_id == user_id,
                )
            )).scalar_one_or_none()
        if not exists:
            title = await generate_conversation_title(content)

    # 第二段短 session：保存 + 推进摘要，用完即闭，期间正是 streaming 开跑之时
    async with AsyncSessionLocal() as session:
        await save_conversation(session_id, user_id, session, storage, title=title)
        # 推进滚动摘要：尾部超长时增量合并旧消息为摘要、游标前移、写回 DB。
        # 增量、持久、每轮对话最多触发一两次，替代旧的「每轮循环重压」。
        summary, cursor = await maybe_advance_summary(
            session_id, user_id, storage, summary, cursor, session
        )

    # 5. 行程卡：立即投递 pending 事件（带 plan_id），done 不再等提取。
    #    提取纯后台跑、写 TravelPlan 表，前端凭 id 走 REST 拉取。
    if is_plan and current_plan_id:
        await queue.put((
            "travel_plans",
            {
                "id": current_plan_id,
                "status": "pending",
                "message": "行程清单生成中,点击查看！",
            },
        ))
        context_snapshot = list(agent_context)
        asyncio.create_task(
            safe_extract_itinerary(context_snapshot, current_plan_id, session_id)
        )

    # 6. 异步更新用户长期记忆（fire-and-forget，不阻塞主响应）
    from agent.memory import update_user_preference
    asyncio.create_task(
        update_user_preference(user_id, agent_context, working_messages)
    )


async def main_model(
    session_id: str,
    user_id: int,
    content: str,
    queue: asyncio.Queue,
) -> Any:
    """Agent 主循环入口 — 包装 _run_agent，保证异常安全 + 完成哨兵。

    无论正常结束还是中途抛错，都先投递 error（若有）再投递 None 哨兵，
    确保 SSE 生成器（agent.py）一定能退出、连接不会挂死。
    """
    from log import logger

    try:
        await _run_agent(session_id, user_id, content, queue)
    except Exception as e:
        logger.exception("agent main_model failed")
        await queue.put(("error", f"处理出错: {e}"))
    finally:
        queue.put_nowait(None)
