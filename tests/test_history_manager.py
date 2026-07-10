from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from services.history_manager import HistoryManager, serialize_native_messages


def row(id: int, turn_id: str, message):
    return SimpleNamespace(
        id=id,
        turn_id=turn_id,
        content="",
        message_data=ModelMessagesTypeAdapter.dump_python([message], mode="json")[0],
    )


def test_native_round_trip_and_turn_grouping():
    messages = [
        ModelRequest(parts=[UserPromptPart(content="question")]),
        ModelResponse(parts=[TextPart(content="answer")], model_name="test"),
    ]
    stored = serialize_native_messages(messages)
    restored = ModelMessagesTypeAdapter.validate_python([item[2] for item in stored])
    assert restored[0].parts[0].content == "question"
    assert restored[1].parts[0].content == "answer"

    rows = [
        row(1, "a", messages[0]),
        row(2, "a", messages[1]),
        row(3, "b", messages[0]),
    ]
    groups = HistoryManager._group_turns(rows)
    assert [len(group) for group in groups] == [2, 1]


async def test_build_keeps_complete_native_history_below_threshold(monkeypatch):
    messages = [
        ModelRequest(parts=[UserPromptPart(content="question")]),
        ModelResponse(parts=[TextPart(content="answer")], model_name="test"),
    ]
    rows = [row(1, "a", messages[0]), row(2, "a", messages[1])]
    message_repo = SimpleNamespace(list_native_history=AsyncMock(return_value=rows))
    conversation_repo = SimpleNamespace()
    conversation = SimpleNamespace(id=1, summary=None, summary_until_message_id=None)
    monkeypatch.setattr(
        "services.history_manager.settings.HISTORY_SUMMARY_TRIGGER_TURNS", 12
    )
    monkeypatch.setattr(
        "services.history_manager.settings.HISTORY_SUMMARY_TRIGGER_TOKENS", 10000
    )

    history = await HistoryManager(message_repo, conversation_repo).build(
        conversation=conversation,
        before_message_id=10,
    )
    assert len(history) == 2
    assert history[0].parts[0].content == "question"


async def test_repository_state_machine_and_native_history():
    from db.session import AsyncSessionLocal
    from repositories import ConversationMessageRepository, ConversationRepository

    async with AsyncSessionLocal() as db:
        conversation, _ = await ConversationRepository(db).get_or_create(
            session_id="native-history-integration",
            user_id=1,
        )
        repo = ConversationMessageRepository(db)
        pending = await repo.create_pending(
            conversation_id=conversation.id,
            user_id=1,
            content="question",
        )
        running = await repo.claim(pending.id)
        assert running.status == "running"

        native = serialize_native_messages(
            [
                ModelRequest(parts=[UserPromptPart(content="question")]),
                ModelResponse(parts=[TextPart(content="answer")], model_name="test"),
            ]
        )
        await repo.complete_turn(pending_id=pending.id, native_messages=native)
        history = await repo.list_native_history(
            conversation_id=conversation.id,
            after_id=None,
            before_id=10**9,
        )
        assert len(history) == 2
        assert all(message.status == "completed" for message in history)

        failed = await repo.create_pending(
            conversation_id=conversation.id,
            user_id=1,
            content="retry me",
        )
        await repo.claim(failed.id)
        await repo.mark_failed(failed.id, "temporary error")
        retried = await repo.claim(failed.id, retry=True)
        assert retried.status == "running"
        assert retried.retry_count == 1
