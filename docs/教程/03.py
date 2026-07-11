"""
03.py — 工具错误处理 & ModelRetry 重试机制

演示 pydantic-ai v2 中工具调用出错的两种处理方式：

1. retries 参数 —— 框架自动重试，ModelRetry 的 message 注入对话历史
2. 手写 ModelRetry —— 工具内部主动抛出，让 LLM 感知错误并修正参数

核心认知：
- 返回错误字符串 → LLM 可能当成正常回复继续，不会重试
- raise ModelRetry  → 框架捕获后构造 RetryPrompt，LLM 读到错误信息后自动修正
- retries=N       → 框架自动管理重试次数，超出后抛 UsageLimitExceeded

运行：
    uv run python 03.py

预期输出：
    get_time ← 正常返回（纯函数）
    risky_search ← 第一次因 ModelRetry 失败，第二次带修正参数成功
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# ── 基础配置 ───────────────────────────────────────────────────────────
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

client = AsyncOpenAI(
    api_key="sk-f46daf0bcea54a15bd5e3fac9cbe7447",
    base_url="https://api.deepseek.com/v1",
)
model = OpenAIChatModel(
    "deepseek-v4-flash",
    provider=OpenAIProvider(openai_client=client),
)
agent = Agent(
    model=model,
    instructions="全程使用中文回答。查询工具结果失败时，根据错误提示修正参数后重试一次。",
)

# ── 三层错误处理对照 ────────────────────────────────────────────────────

# ① 纯工具 —— 正常返回（参考实现）
@agent.tool_plain
def get_time() -> str:
    """获取当前时间"""
    return datetime.now(tz=CHINA_TZ).isoformat(timespec="seconds")


# ② retries 自动重试示范 —— 框架自动捕获 ModelRetry 并重试
@agent.tool_plain(retries=2)
def safe_divide(a: float, b: float) -> str:
    """安全除法，除数为 0 时提示 LLM 重新提供参数

    框架会最多重试 2 次。每次重试时，ModelRetry 的 message
    会以 RetryPrompt 形式注入对话历史，LLM 读到后自动修正参数。
    """
    if b == 0:
        raise ModelRetry("除数不能为0，请提供一个非零的除数")
    return f"{a} / {b} = {a / b}"


# ── SSE 输出 ──────────────────────────────────────────────────────────
async def main():
    # 故意让 LLM 计算 10/0，触发 safe_divide 的 ModelRetry。
    # 第一次调用 b=0 → raise ModelRetry，
    # 框架注入 RetryPrompt 告诉 LLM "除数不能为0"，
    # LLM 读到错误后自动修正参数再试一次。
    question = "获取当前时间，然后用 safe_divide 计算 10/0，接着搜索 Python asyncio"

    async with agent.run_stream_events(question) as events:
        async for event in events:
            if event.event_kind == "part_delta":
                delta = event.delta
                kind = getattr(delta, "part_delta_kind", None)

                if kind in ("text", "thinking"):
                    content = getattr(delta, "content_delta", None)
                    if content:
                        print(content, end="", flush=True)

                elif kind == "tool_call":
                    tool_name = getattr(delta, "tool_name_delta", None)
                    if tool_name:
                        print(f"\n  🔧 准备调用: {tool_name}")

            elif event.event_kind == "function_tool_call":
                print(f"\n  📞 调用工具: {event.part.tool_name}")
                print(f"     参数: {event.part.args}")

            elif event.event_kind == "function_tool_result":
                print(f"  ✅ 返回结果: {event.content}")

            elif event.event_kind == "agent_run_result":
                usage = event.result.usage
                print(f"\n\n{'─' * 40}")
                print(f"  输入 token:  {usage.input_tokens}")
                print(f"  输出 token:  {usage.output_tokens}")
                print(f"  总 token:    {usage.total_tokens}")
                print(f"  请求次数:    {usage.requests}")
                print(f"  工具调用:    {usage.tool_calls}")


if __name__ == "__main__":
    asyncio.run(main())
