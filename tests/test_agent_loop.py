"""Tests for agent service — non-streaming path using pydantic-ai agent.run()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.model_client import ModelClientError
from agent.prompts import default_system_prompt
from schemas.agent import ChatRequest


async def test_run_with_missing_model_key_raises():
    """AgentService.run() raises ModelClientError when no API key is configured."""
    from services.agent_service import AgentService

    mock_db = AsyncMock()

    payload = ChatRequest(message="hello", session_id="sess-1")

    with (
        patch("services.agent_service.ConversationRepository") as conv_repo_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_repo_cls,
        patch("services.agent_service.AgentRunRepository") as run_repo_cls,
        patch("services.agent_service.get_agent") as mock_get_agent,
    ):
        conv_repo = conv_repo_cls.return_value
        conv_repo.get_or_create = AsyncMock(return_value=(MagicMock(id=1), False))

        msg_repo = msg_repo_cls.return_value
        msg_repo.insert = AsyncMock(return_value=MagicMock(id=10))
        msg_repo.list_by_conversation = AsyncMock(return_value=[])

        run_repo = run_repo_cls.return_value
        run_repo.create = AsyncMock(return_value=MagicMock(id=5))
        run_repo.mark_failed = AsyncMock()

        mock_get_agent.side_effect = ModelClientError(
            "Agent model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
        )

        service = AgentService(mock_db)

        with pytest.raises(ModelClientError, match="not configured"):
            await service.run(payload, user_id=1)


def test_normalize_ag_ui_body_accepts_simple_stream_payload():
    from services.agent_service import AgentService

    body = AgentService._normalize_ag_ui_body({"message": "hello"}, user_id=123)

    assert body["messages"] == [
        {
            "role": "user",
            "content": "hello",
            "id": body["messages"][0]["id"],
            "user": {"id": "123"},
        }
    ]
    assert body["threadId"]
    assert body["runId"]
    assert body["state"] == {}
    assert body["tools"] == []
    assert body["context"] == []
    assert body["forwardedProps"] == {}


def test_normalize_ag_ui_body_preserves_optional_thread_when_present():
    from services.agent_service import AgentService

    body = AgentService._normalize_ag_ui_body(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                }
            ],
            "thread_id": "existing-session",
        },
        user_id=123,
    )

    assert body["threadId"] == "existing-session"
    assert body["messages"][0]["id"]
    assert body["messages"][0]["user"]["id"] == "123"


def test_init_agent_requires_model_key(monkeypatch):
    from agent import model_client

    monkeypatch.setattr(model_client, "_agent", None)
    monkeypatch.setattr("agent.model_client.settings.DEEP_SEEK_API_KEY", "")
    monkeypatch.setattr("agent.model_client.settings.API_KEY", "")

    with pytest.raises(ModelClientError, match="not configured"):
        model_client.init_agent()


def test_init_agent_caches_agent(monkeypatch):
    from agent import model_client

    fake_agent = MagicMock()
    fake_agent.tool_plain.return_value = lambda func: func

    monkeypatch.setattr(model_client, "_agent", None)
    monkeypatch.setattr("agent.model_client.settings.DEEP_SEEK_API_KEY", "test-key")
    monkeypatch.setattr("agent.model_client.settings.API_KEY", "")
    monkeypatch.setattr("agent.model_client.AsyncOpenAI", MagicMock())
    monkeypatch.setattr("agent.model_client.OpenAIProvider", MagicMock())
    monkeypatch.setattr("agent.model_client.OpenAIChatModel", MagicMock())
    monkeypatch.setattr("agent.model_client.Agent", MagicMock(return_value=fake_agent))

    first = model_client.init_agent()
    second = model_client.init_agent()

    assert first is second
    assert first is fake_agent
    assert fake_agent.tool_plain.call_count == 2


async def test_run_returns_answer_from_agent():
    """AgentService.run() returns the agent's output as the answer."""
    from services.agent_service import AgentService

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()

    with (
        patch("services.agent_service.ConversationRepository") as conv_repo_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_repo_cls,
        patch("services.agent_service.AgentRunRepository") as run_repo_cls,
        patch("services.agent_service.get_agent") as mock_get_agent,
    ):
        conv_repo = conv_repo_cls.return_value
        conv_repo.get_or_create = AsyncMock(return_value=(MagicMock(id=1), False))
        conv_repo.touch = AsyncMock()

        msg_repo = msg_repo_cls.return_value
        msg_repo.insert = AsyncMock(return_value=MagicMock(id=10))
        msg_repo.list_by_conversation = AsyncMock(return_value=[])

        run_repo = run_repo_cls.return_value
        run_repo.create = AsyncMock(return_value=MagicMock(id=5))
        run_repo.mark_succeeded = AsyncMock()
        run_repo.mark_failed = AsyncMock()

        # Mock agent.run() to return a result with new_messages()
        fake_agent = MagicMock()
        fake_result = MagicMock()
        fake_result.output = "The capital of France is Paris."
        fake_result.new_messages.return_value = []
        fake_agent.run = AsyncMock(return_value=fake_result)
        mock_get_agent.return_value = fake_agent

        service = AgentService(mock_db)
        payload = ChatRequest(message="What is the capital of France?", session_id="sess-1")

        result = await service.run(payload, user_id=1)

        assert result["answer"] == "The capital of France is Paris."
        assert result["session_id"] == "sess-1"
        assert result["tool_calls"] == []

        # Verify DB writes happened
        run_repo.create.assert_called_once()
        assert msg_repo.insert.call_count == 2  # user msg + assistant msg
        run_repo.mark_succeeded.assert_called_once()
        conv_repo.touch.assert_called_once()

        # Verify agent.run() was called with message_history
        fake_agent.run.assert_called_once()
        _, kwargs = fake_agent.run.call_args
        assert "message_history" in kwargs
        assert kwargs["instructions"] == default_system_prompt()


async def test_run_marks_failed_on_agent_error():
    """AgentService.run() marks the run as failed when agent.run() raises."""
    from services.agent_service import AgentService

    mock_db = AsyncMock()

    with (
        patch("services.agent_service.ConversationRepository") as conv_repo_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_repo_cls,
        patch("services.agent_service.AgentRunRepository") as run_repo_cls,
        patch("services.agent_service.get_agent") as mock_get_agent,
    ):
        conv_repo = conv_repo_cls.return_value
        conv_repo.get_or_create = AsyncMock(return_value=(MagicMock(id=1), False))
        conv_repo.touch = AsyncMock()

        msg_repo = msg_repo_cls.return_value
        msg_repo.insert = AsyncMock(return_value=MagicMock(id=10))
        msg_repo.list_by_conversation = AsyncMock(return_value=[])

        run_repo = run_repo_cls.return_value
        run_repo.create = AsyncMock(return_value=MagicMock(id=5))
        run_repo.mark_failed = AsyncMock()

        fake_agent = MagicMock()
        fake_agent.run = AsyncMock(side_effect=RuntimeError("model exploded"))
        mock_get_agent.return_value = fake_agent

        service = AgentService(mock_db)
        payload = ChatRequest(message="hello", session_id="sess-1")

        with pytest.raises(RuntimeError, match="model exploded"):
            await service.run(payload, user_id=1)

        run_repo.mark_failed.assert_called_once()
