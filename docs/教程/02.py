"""
02.py — FastAPI SSE 流式 Chat 接口

演示如何将 pydantic-ai v2 的 run_stream_events() 封装为标准的
text/event-stream (SSE) 接口，供前端 EventSource 消费。

运行：
    uv run python 02.py

测试：
    curl -X POST http://localhost:8000/chat/stream \
         -H "Content-Type: application/json" \
         -d '{"message":"现在几点？用一句话介绍asyncio"}'
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# ── 中国时区（不依赖 Windows zoneinfo/tzdata）────────────────────────
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# ── Agent 配置（与 01.py 一致）──────────────────────────────────────
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
    instructions="全程使用中文回答。用户问当前时间时，必须调用 get_time 工具。",
)


@agent.tool_plain
def get_time() -> str:
    """获取当前时间"""
    return datetime.now(tz=CHINA_TZ).isoformat(timespec="seconds")


# ── FastAPI 应用 ────────────────────────────────────────────────────
app = FastAPI(title="PydanticAI SSE Chat Demo")


@app.post("/chat/stream", summary="SSE 流式对话")
async def chat_stream(request: Request):
    """标准 text/event-stream 接口。

    前端 EventSource / fetch + ReadableStream 可直接消费。
    """
    body = await request.json()
    user_message = body.get("message", "")

    async def event_stream():
        # run_stream_events 产生 AgentStreamEvent 事件对象
        async with agent.run_stream_events(user_message) as events:
            async for event in events:
                # ── 文本 / 推理增量 ──────────────────────────────
                if event.event_kind == "part_delta":
                    delta = event.delta
                    kind = getattr(delta, "part_delta_kind", None)

                    if kind in ("text", "thinking"):
                        content = getattr(delta, "content_delta", None)
                        if content:
                            event_type = (
                                "TEXT_MESSAGE_CONTENT"
                                if kind == "text"
                                else "REASONING_MESSAGE_CONTENT"
                            )
                            yield _sse(event_type, delta=content)

                    elif kind == "tool_call":
                        # 流式工具调用中的增量 delta（工具名 / 参数逐步到达）
                        tool_name = getattr(delta, "tool_name_delta", None)
                        if tool_name:
                            yield _sse("TOOL_CALL_NAME", tool_name=tool_name)
                        args_delta = getattr(delta, "args_delta", None)
                        if args_delta:
                            yield _sse("TOOL_CALL_ARGS", args=args_delta)

                # ── 工具调用开始 ──────────────────────────────────
                elif event.event_kind == "function_tool_call":
                    yield _sse(
                        "TOOL_CALL_START",
                        tool_name=event.part.tool_name,
                        args=event.part.args,
                    )

                # ── 工具调用返回 ──────────────────────────────────
                elif event.event_kind == "function_tool_result":
                    yield _sse(
                        "TOOL_CALL_RESULT",
                        tool_name=event.part.tool_name,
                        content=event.content,
                    )

                # ── 流结束 + token 统计 ──────────────────────────
                elif event.event_kind == "agent_run_result":
                    usage = event.result.usage
                    yield _sse(
                        "RUN_FINISHED",
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        total_tokens=usage.total_tokens,
                    )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── SSE 编码辅助 ─────────────────────────────────────────────────────
def _sse(event_type: str, **kwargs) -> str:
    """编码为 SSE 数据行。"""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 一键启动 ─────────────────────────────────────────────────────────
# 判断当前文件是「直接被运行」还是「被别的文件 import 导入
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("02:app", host="0.0.0.0", port=8000, reload=True)
