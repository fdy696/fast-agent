"""Tests for agent service — non-streaming path using pydantic-ai agent.run()."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent.model_client import ModelClientError
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
        patch("services.agent_service.build_agent") as mock_create,
        patch("services.agent_service.default_system_prompt", return_value="You are helpful."),
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

        mock_create.side_effect = ModelClientError(
            "Agent model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
        )

        service = AgentService(mock_db)

        with pytest.raises(ModelClientError, match="not configured"):
            await service.run(payload, user_id=1)


async def test_run_returns_answer_from_agent():
    """AgentService.run() returns the agent's output as the answer."""
    from services.agent_service import AgentService

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()

    # Mock repos — agent_service creates repos in __init__
    with (
        patch("services.agent_service.ConversationRepository") as conv_repo_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_repo_cls,
        patch("services.agent_service.AgentRunRepository") as run_repo_cls,
        patch("services.agent_service.build_agent") as mock_create,
        patch("services.agent_service.default_system_prompt", return_value="You are helpful."),
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

        # Mock the agent — build_agent() returns the agent directly
        fake_agent = MagicMock()
        fake_result = MagicMock()
        fake_result.output = "The capital of France is Paris."
        fake_agent.run = AsyncMock(return_value=fake_result)
        fake_agent.tool = MagicMock()
        mock_create.return_value = fake_agent

        service = AgentService(mock_db)
        payload = ChatRequest(message="What is the capital of France?", session_id="sess-1")

        result = await service.run(payload, user_id=1)

        assert result["answer"] == "The capital of France is Paris."
        assert result["session_id"] == "sess-1"

        # Verify DB writes happened
        run_repo.create.assert_called_once()
        assert msg_repo.insert.call_count == 2  # user msg + assistant msg
        run_repo.mark_succeeded.assert_called_once()
        conv_repo.touch.assert_called_once()


async def test_run_marks_failed_on_agent_error():
    """AgentService.run() marks the run as failed when agent.run() raises."""
    from services.agent_service import AgentService

    mock_db = AsyncMock()

    with (
        patch("services.agent_service.ConversationRepository") as conv_repo_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_repo_cls,
        patch("services.agent_service.AgentRunRepository") as run_repo_cls,
        patch("services.agent_service.build_agent") as mock_create,
        patch("services.agent_service.default_system_prompt", return_value="You are helpful."),
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
        fake_agent.tool = MagicMock()
        mock_create.return_value = fake_agent

        service = AgentService(mock_db)
        payload = ChatRequest(message="hello", session_id="sess-1")

        with pytest.raises(RuntimeError, match="model exploded"):
            await service.run(payload, user_id=1)

        run_repo.mark_failed.assert_called_once()
