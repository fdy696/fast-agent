"""PydanticAI model binding for OpenAI-compatible providers."""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from core.config import settings
from agent.tools import ask_human, current_time


class ModelClientError(RuntimeError):
    pass


_agent: Agent | None = None


def init_agent() -> Agent:
    """Initialize and cache the process-wide PydanticAI agent."""
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
    agent = Agent(model)
    agent.tool_plain(retries=3)(current_time)
    agent.tool_plain(retries=2)(ask_human)

    _agent = agent
    return _agent


def get_agent() -> Agent:
    """Return the initialized process-wide agent."""
    return init_agent()


def build_agent(*, system_prompt: str | None = None) -> Agent:
    """Compatibility wrapper for older call sites."""
    return get_agent()
