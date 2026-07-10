"""Persistence tests for the rebuilt chat history state machine."""

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)


async def test_repository_state_machine_and_native_history():
    from db.session import AsyncSessionLocal
    from repositories.chat_message import ChatMessageRepository
    from repositories.chat_session import ChatSessionRepository

    async with AsyncSessionLocal() as db:
        session = await ChatSessionRepository(db).create(
            session_id="native-history-integration",
            user_id=1,
        )
        repo = ChatMessageRepository(db)
        pending = await repo.insert(
            session_id=session.session_id,
            turn_id="turn-1",
            message_index=0,
            status="pending",
            role="user",
            content="question",
        )
        running = await repo.get_by_id_for_update(pending.id)
        assert running is not None
        await repo.update_status(running, "running")
        assert running.status == "running"

        native = [
            ModelRequest(parts=[UserPromptPart(content="question")]),
            ModelResponse(parts=[TextPart(content="answer")], model_name="test"),
        ]
        serialized = ModelMessagesTypeAdapter.dump_python(native, mode="json")
        await repo.update_message_data(running, message_data=serialized[0])
        await repo.update_status(running, "completed")
        await repo.insert(
            session_id=session.session_id,
            turn_id="turn-1",
            message_index=1,
            status="completed",
            role="assistant",
            content="answer",
            message_data=serialized[1],
        )

        history = await repo.list_completed_turns(
            session_id=session.session_id,
            before_message_id=10**9,
        )
        assert len(history) == 2
        restored = ModelMessagesTypeAdapter.validate_python(
            [message.message_data for message in history]
        )
        assert restored[0].parts[0].content == "question"
        assert restored[1].parts[0].content == "answer"
