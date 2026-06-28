"""PydanticAI model binding for OpenAI-compatible providers."""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from core.config import settings


class ModelClientError(RuntimeError):
    pass


def build_agent(*, system_prompt: str) -> Agent:
    """Create a PydanticAI Agent with the configured model and instructions.

    Tools are registered separately via ``agent.tool(func)``.
    """
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
    return Agent(model, instructions=system_prompt)
