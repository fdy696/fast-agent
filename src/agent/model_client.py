from typing import Any, Iterable, Literal, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from core.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.DEEP_SEEK_API_KEY or settings.API_KEY or "dummy",
            base_url=settings.AGENT_BASE_URL,
        )
    return _client


async def call_model(
    messages: Iterable[ChatCompletionMessageParam],
    tools: Iterable[ChatCompletionToolUnionParam],
    response_format: Literal["text", "json_object"],
    stream: bool = True,
    tool_choice: Literal["auto", "none"] = "auto",
    model: str = "",
) -> Any:
    """通用模型调用，支持 stream / json_object"""
    return await _get_client().chat.completions.create(
        model=model or settings.AGENT_MODEL,
        messages=messages,
        tools=tools,
        stream=stream,
        temperature=0.2,
        response_format=cast(Any, {"type": response_format}),
        tool_choice=tool_choice,
        stream_options={"include_usage": True},
    )


async def call_compress_model(
    messages: Iterable[ChatCompletionMessageParam],
) -> Any:
    """压缩模型调用"""
    return await _get_client().chat.completions.create(
        model=settings.COMPRESS_MODEL,
        stream=False,
        messages=messages,
    )
