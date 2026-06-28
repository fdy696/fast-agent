"""消费 LLM 流式输出：分离文本内容与 tool_calls 增量"""

import asyncio
from typing import List

from openai import AsyncStream
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageToolCallParam


async def consume_stream(
    stream: AsyncStream[ChatCompletionChunk],
    queue: asyncio.Queue,
) -> tuple[str, List[ChatCompletionMessageToolCallParam], dict | None]:
    full_content = ""
    tool_calls: List[ChatCompletionMessageToolCallParam] = []
    usage: dict | None = None

    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                usage = {
                    "prompt_tokens": u.prompt_tokens or 0,
                    "completion_tokens": u.completion_tokens or 0,
                    "cached_tokens": getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0,
                }
            continue
        if delta.content:
            full_content += delta.content
            await queue.put(("assistant", delta.content))
        if delta.tool_calls:
            for tool_delta in delta.tool_calls:
                index = tool_delta.index
                while len(tool_calls) <= index:
                    tool_calls.append({
                        "id": "",
                        "function": {"name": "", "arguments": ""},
                        "type": "function",
                    })
                if tool_delta.id:
                    tool_calls[index]["id"] = tool_delta.id
                if tool_delta.function:
                    if tool_delta.function.name:
                        tool_calls[index]["function"]["name"] += tool_delta.function.name
                    if tool_delta.function.arguments:
                        tool_calls[index]["function"]["arguments"] += tool_delta.function.arguments

    return full_content, tool_calls, usage
