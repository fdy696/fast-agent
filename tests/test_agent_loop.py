"""Agent orchestration tests for native history and durable state."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent.model_client import ModelClientError
from schemas.agent import ChatRequest


def test_normalize_ag_ui_body_accepts_simple_payload():
    from services.agent_service import AgentService

    body = AgentService._normalize_ag_ui_body({"message": "hello"}, user_id=123)
    assert body["messages"][0]["content"] == "hello"
    assert body["messages"][0]["user"]["id"] == "123"
    assert body["threadId"]
    assert body["runId"]


def test_normalize_ag_ui_body_discards_untrusted_client_history():
    from services.agent_service import AgentService

    body = AgentService._normalize_ag_ui_body(
        {
            "threadId": "thread-1",
            "messages": [
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "current question"},
            ],
        },
        user_id=123,
    )
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "current question"


def test_init_agent_requires_model_key(monkeypatch):
    from agent import model_client

    monkeypatch.setattr(model_client, "_agent", None)
    monkeypatch.setattr("agent.model_client.settings.DEEP_SEEK_API_KEY", "")
    monkeypatch.setattr("agent.model_client.settings.API_KEY", "")
    with pytest.raises(ModelClientError, match="not configured"):
        model_client.init_agent()


async def test_run_persists_native_messages():
    from services.agent_service import AgentService

    mock_db = AsyncMock()
    conversation = MagicMock(
        id=1, session_id="sess-1", summary=None, summary_until_message_id=None
    )
    pending = MagicMock(id=10, conversation_id=1, user_id=1, content="hello")
    native = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[TextPart(content="hi")], model_name="test"),
    ]
    result = MagicMock(output="hi")
    result.new_messages.return_value = native
    agent = MagicMock()
    agent.run = AsyncMock(return_value=result)

    with (
        patch("services.agent_service.ConversationRepository") as conv_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_cls,
        patch("services.agent_service.HistoryManager") as history_cls,
        patch("services.agent_service.get_agent", return_value=agent),
    ):
        conv_cls.return_value.get_or_create = AsyncMock(
            return_value=(conversation, False)
        )
        conv_cls.return_value.get_by_id = AsyncMock(return_value=conversation)
        conv_cls.return_value.touch = AsyncMock()
        msg_cls.return_value.create_pending = AsyncMock(return_value=pending)
        msg_cls.return_value.claim = AsyncMock(return_value=pending)
        msg_cls.return_value.complete_turn = AsyncMock(return_value=MagicMock(id=11))
        msg_cls.return_value.mark_failed = AsyncMock()
        history_cls.return_value.build = AsyncMock(return_value=[])

        response = await AgentService(mock_db).run(
            ChatRequest(message="hello", session_id="sess-1"), user_id=1
        )

        assert response["answer"] == "hi"
        msg_cls.return_value.create_pending.assert_awaited_once()
        msg_cls.return_value.claim.assert_awaited_once_with(10)
        msg_cls.return_value.complete_turn.assert_awaited_once()
        call = msg_cls.return_value.complete_turn.await_args.kwargs
        assert call["pending_id"] == 10
        assert len(call["native_messages"]) == 2
        assert call["native_messages"][0][0] == "user"
        assert call["native_messages"][1][0] == "assistant"


async def test_run_marks_pending_message_failed():
    from services.agent_service import AgentService

    mock_db = AsyncMock()
    conversation = MagicMock(
        id=1, session_id="sess-1", summary=None, summary_until_message_id=None
    )
    pending = MagicMock(id=10, conversation_id=1, user_id=1, content="hello")
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=RuntimeError("model exploded"))

    with (
        patch("services.agent_service.ConversationRepository") as conv_cls,
        patch("services.agent_service.ConversationMessageRepository") as msg_cls,
        patch("services.agent_service.HistoryManager") as history_cls,
        patch("services.agent_service.get_agent", return_value=agent),
    ):
        conv_cls.return_value.get_or_create = AsyncMock(
            return_value=(conversation, False)
        )
        msg_cls.return_value.create_pending = AsyncMock(return_value=pending)
        msg_cls.return_value.claim = AsyncMock(return_value=pending)
        msg_cls.return_value.mark_failed = AsyncMock()
        history_cls.return_value.build = AsyncMock(return_value=[])

        with pytest.raises(RuntimeError, match="model exploded"):
            await AgentService(mock_db).run(
                ChatRequest(message="hello", session_id="sess-1"), user_id=1
            )
        msg_cls.return_value.mark_failed.assert_awaited_once_with(10, "model exploded")
