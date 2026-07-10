"""Focused tests for the current ChatService/PydanticAI integration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from agent.model_client import ModelClientError
from services.chat_service import build_model_history


def _stored(message):
    return ModelMessagesTypeAdapter.dump_python([message], mode="json")[0]


def test_init_agent_requires_model_key(monkeypatch):
    from agent import model_client

    monkeypatch.setattr(model_client, "_agent", None)
    monkeypatch.setattr("agent.model_client.settings.DEEP_SEEK_API_KEY", "")
    monkeypatch.setattr("agent.model_client.settings.API_KEY", "")
    with pytest.raises(ModelClientError, match="not configured"):
        model_client.init_agent()


async def test_build_history_uses_authenticated_user_scope():
    session = SimpleNamespace(
        history_summary=None,
        summarized_through_message_id=None,
    )
    rows = [
        SimpleNamespace(
            id=1,
            turn_id="turn-1",
            content="question",
            message_data=_stored(
                ModelRequest(parts=[UserPromptPart(content="question")])
            ),
        ),
        SimpleNamespace(
            id=2,
            turn_id="turn-1",
            content="answer",
            message_data=_stored(
                ModelResponse(parts=[TextPart(content="answer")], model_name="test")
            ),
        ),
    ]

    with (
        patch("services.chat_service.ChatSessionRepository") as session_cls,
        patch("services.chat_service.ChatMessageRepository") as message_cls,
    ):
        session_cls.return_value.get_by_session_id = AsyncMock(return_value=session)
        message_cls.return_value.list_completed_turns = AsyncMock(return_value=rows)

        history = await build_model_history(
            AsyncMock(), session_id="session-1", user_id=42, before_message_id=3
        )

    session_cls.return_value.get_by_session_id.assert_awaited_once_with(
        session_id="session-1", user_id=42
    )
    assert history[0].parts[0].content == "question"
    assert history[1].parts[0].content == "answer"


async def test_debug_token_is_not_available_in_test_environment(client):
    response = await client.get("/debug/token")
    assert response.status_code == 404
