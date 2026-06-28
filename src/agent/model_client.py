"""PydanticAI model binding for OpenAI-compatible providers."""

from __future__ import annotations

from typing import Any, Protocol

from core.config import settings


class ModelClientError(RuntimeError):
    pass


class ModelClient(Protocol):
    def build_agent(self, *, system_prompt: str, tools: list[Any]):
        ...


class PydanticAIModelClient:
    def build_agent(self, *, system_prompt: str, tools: list[Any]):
        if not settings.DEEP_SEEK_API_KEY and not settings.API_KEY:
            raise ModelClientError(
                "Agent model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
            )

        try:
            from openai import AsyncOpenAI
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider
        except ImportError as exc:
            raise ModelClientError(
                "PydanticAI is not installed. Install dependencies from pyproject.toml."
            ) from exc

        client = AsyncOpenAI(
            api_key=settings.DEEP_SEEK_API_KEY or settings.API_KEY,
            base_url=settings.AGENT_BASE_URL,
        )
        model = OpenAIChatModel(
            settings.AGENT_MODEL,
            provider=OpenAIProvider(openai_client=client),
        )
        return Agent(model, tools=tools, instructions=system_prompt)


def create_model_client() -> ModelClient:
    return PydanticAIModelClient()
