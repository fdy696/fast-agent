"""上下文窗口安全网：token 计数 + 硬裁剪。

滚动摘要 + 游标由 context_store.maybe_advance_summary 在每轮对话结束后
增量推进并持久化，正常情况下加载到模型的上下文已被约束在预算内。
本模块仅作为安全网：当单轮工具结果等极端情况导致总量仍超预算时，
从最旧消息开始硬裁剪，且配对感知（不留下孤立的 tool 消息）。
"""

from typing import Any, Dict, List

import tiktoken

from core.config import settings

TOKEN_BUDGET = getattr(settings, "CONTEXT_MAX_TOKENS", 28000)

# tokenizer（cl100k 覆盖 qwen / deepseek / gpt 系列，偏差 < 5%）
_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(messages: List[Dict[str, Any]]) -> int:
    """计算消息列表的总 token 数"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(_enc.encode(content))
        # tool_calls 也消耗 token
        if msg.get("tool_calls"):
            total += len(_enc.encode(str(msg["tool_calls"])))
    return total


async def manage_context(
    messages: List[Dict[str, Any]],
    max_tokens: int = TOKEN_BUDGET,
) -> List[Dict[str, Any]]:
    """安全网：总量超预算时从最旧非 system 消息开始硬裁剪。

    约定 messages[0] 是 system prompt（不可裁剪）。无 LLM 调用。
    """
    if not messages:
        return messages

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system_msg else messages

    total = count_tokens(messages)
    if total <= max_tokens:
        return messages

    body = _trim_oldest(body, max_tokens, system_msg)
    return [system_msg] + body if system_msg else body


def _trim_oldest(
    body: List[Dict[str, Any]],
    max_tokens: int,
    system_msg: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """硬裁剪：从最早的非 system 消息开始逐条删除，直到 token 数达标。

    配对感知：若删除一条 assistant(tool_calls) 后下一条是 tool 消息，
    一并删除，避免留下孤立 tool 消息导致网关 400。
    """
    sys_tokens = count_tokens([system_msg]) if system_msg else 0
    budget = max_tokens - sys_tokens

    kept = list(body)
    while kept and count_tokens(kept) > budget:
        removed = kept.pop(0)
        # 若删的是带 tool_calls 的 assistant，且下一条是 tool，连带删掉
        if removed.get("role") == "assistant" and removed.get("tool_calls"):
            while kept and kept[0].get("role") == "tool":
                kept.pop(0)
        if not kept:
            break
    return kept
