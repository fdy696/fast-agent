"""跨会话长期记忆 — 结构化偏好提取

设计原则
────────
- 不阻塞对话：fire-and-forget，失败不影响主流程
- 仅提取用户消息：不读模型输出，避免幻觉污染
- 结构化 JSONB：7 个字段区分长期偏好 vs 单次参数
- 渐进合并：已有 dict + 本轮提取 → 同字段以新覆盖旧
"""

import json
from typing import Any, Dict, List

from agent.model_client import call_compress_model
from agent.model_prompt import build_memory_field_hints
from db.session import AsyncSessionLocal
from log import logger
from repositories.user import UserRepository

MEMORY_PROMPT = """你是一个用户偏好分析专家。

【已有偏好 JSON】
{existing}

【用户对话消息】
{conversation}

【任务】
根据用户的消息（role: user），提取以下长期偏好，输出 JSON：

字段规则（只有用户明确表示过才填，否则用 null）：

{field_hints}

【严禁提取】
以下信息是单次行程参数，绝对不能写入任何字段：
- 出行日期、月份、季节
- 出发城市
- 同行具体人数（如"3人"、"一家四口"）
- 本次想去的具体目的地（如"我想去贵州"中的"贵州"）——这不是长期偏好，是本次需求
- 具体的酒店名、景点名

【合并规则】
对每个字段，如果用户本轮有新信息则覆盖旧值；如果本轮没有提及则保留已有值。
如果用户本轮的消息里没有任何可提取的长期偏好，返回已有的 JSON 原样。

只返回 JSON，不要加任何解释。"""

ALLOWED_KEYS = {
    "budget",
    "travel_style",
    "dietary_restrictions",
    "transport",
    "accommodation",
    "travel_companion",
    "visited_destinations",
}


async def update_user_preference(
    user_id: int,
    agent_context: List[Dict[str, Any]],
    working_messages: List[Dict[str, Any]],
) -> None:
    """
    从用户消息中提取结构化长期偏好，渐进式更新 User.preference 字段。

    调用时机：agent 主循环结束后（fire-and-forget）。
    """
    try:
        # 1. 读取已有偏好（JSONB 列，直接返回 dict）
        async with AsyncSessionLocal() as db:
            repo = UserRepository(db)
            existing = await repo.get_preference(user_id) or {}

            # 2. 只提取用户消息
            parts: list[str] = []
            for msg in agent_context + working_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and content:
                    parts.append(str(content)[:300])

            if not parts:
                return

            conversation_snippet = "\n".join(parts[-8:])  # 最多取最近 8 条用户消息

            # 3. 调用压缩模型提取结构化偏好（字段提示从统一数据源注入）
            prompt = MEMORY_PROMPT.format(
                existing=json.dumps(existing, ensure_ascii=False),
                conversation=conversation_snippet,
                field_hints=build_memory_field_hints(),
            )
            res = await call_compress_model(
                messages=[{"role": "system", "content": prompt}],
            )

            raw = (res.choices[0].message.content or "").strip()
            # 去除 markdown 代码块包裹
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
                else:
                    raw = raw.rstrip("`").strip()

            new_pref: dict[str, Any]
            try:
                new_pref = json.loads(raw)
                if not isinstance(new_pref, dict):
                    return
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"memory structured extraction failed for user {user_id}: {raw[:200]}"
                )
                return

            # 4. 白名单过滤 + 清理 null/空值
            cleaned = {k: new_pref.get(k) for k in ALLOWED_KEYS}
            cleaned = {
                k: v for k, v in cleaned.items() if v is not None and v != [] and v != ""
            }

            if not cleaned:
                return

            if cleaned != existing:
                await repo.update_preference(user_id, cleaned)
                logger.info(f"updated preference for user {user_id}: {cleaned}")
    except Exception as e:
        logger.warning(f"memory update failed (non-blocking): {e}")
