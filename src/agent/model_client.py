"""PydanticAI model binding for OpenAI-compatible providers."""

from __future__ import annotations

from typing import Iterable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from agent.tools import ask_human, current_time
from core.config import settings
from agent.tool_guard import make_guarded
from agent.mcp import get_mcp_toolsets
from agent.skills.toolset import SkillToolset


class ModelClientError(RuntimeError):
    pass


_agent: Agent | None = None
_compress_agent: Agent | None = None
_compress_client: AsyncOpenAI | None = None


def init_agent(registry=None) -> Agent:
    global _agent
    if _agent is not None:
        return _agent

    if not settings.DEEP_SEEK_API_KEY and not settings.API_KEY:
        raise ModelClientError(
            "Agent model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
        )

    client = AsyncOpenAI(
        api_key=settings.DEEP_SEEK_API_KEY or settings.API_KEY,
        base_url=settings.AGENT_BASE_URL,
    )
    model = OpenAIChatModel(
        settings.AGENT_MODEL,
        provider=OpenAIProvider(openai_client=client),
    )
    toolsets = [*get_mcp_toolsets()]
    if registry is not None:
        toolsets.append(SkillToolset(registry))
    from pydantic_ai.capabilities import ReinjectSystemPrompt

    agent = Agent(
        model,
        toolsets=toolsets,
        capabilities=[ReinjectSystemPrompt()],
    )
    agent.tool_plain(retries=3)(make_guarded("current_time", current_time))
    agent.tool_plain(retries=2)(make_guarded("ask_human", ask_human))

    _agent = agent
    return _agent


def get_agent() -> Agent:
    return init_agent()


def get_compress_agent() -> Agent:
    """Return a cheaper agent dedicated to incremental history summaries."""
    global _compress_agent
    if _compress_agent is not None:
        return _compress_agent
    if not settings.DEEP_SEEK_API_KEY and not settings.API_KEY:
        raise ModelClientError("Compression model is not configured.")
    client = AsyncOpenAI(
        api_key=settings.DEEP_SEEK_API_KEY or settings.API_KEY,
        base_url=settings.AGENT_BASE_URL,
    )
    model = OpenAIChatModel(
        settings.COMPRESS_MODEL,
        provider=OpenAIProvider(openai_client=client),
    )
    _compress_agent = Agent(model)
    return _compress_agent


def build_agent(*, system_prompt: str | None = None) -> Agent:
    return get_agent()


async def call_compress_model(
    messages: Iterable,
) -> ChatCompletion:
    global _compress_client
    if _compress_client is None:
        api_key = settings.DEEP_SEEK_API_KEY or settings.API_KEY
        if not api_key:
            raise ModelClientError(
                "Compress model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
            )
        _compress_client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.AGENT_BASE_URL,
        )
    return await _compress_client.chat.completions.create(
        model=settings.COMPRESS_MODEL,
        stream=False,
        messages=messages,
    )
