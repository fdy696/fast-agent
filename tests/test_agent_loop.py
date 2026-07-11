"""Unit tests for PydanticAI-native run persistence."""

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python

from repositories.chat import MessageBatch, flatten_batches, is_reusable_run
from services.chat_service import current_run_messages, to_ui_messages


def complete_run(run_id: str = "run-1"):
    return [
        ModelRequest(
            parts=[UserPromptPart(content="question")],
            run_id=run_id,
        ),
        ModelResponse(
            parts=[TextPart(content="answer")],
            model_name="test",
            run_id=run_id,
        ),
    ]


def test_jsonb_round_trip_preserves_native_messages():
    messages = complete_run()
    stored = to_jsonable_python(messages)
    restored = ModelMessagesTypeAdapter.validate_python(stored)

    assert restored == messages
    assert isinstance(
        ModelMessagesTypeAdapter.dump_python(messages, mode="json"), list
    )


def test_only_semantically_complete_runs_are_reusable():
    assert is_reusable_run(complete_run())

    tool_only = [
        ModelResponse(
            parts=[ToolCallPart("weather", {"city": "Beijing"}, "call-1")],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart("weather", "sunny", "call-1")]
        ),
    ]
    assert not is_reusable_run(tool_only)

    interrupted = [
        complete_run()[0],
        ModelResponse(
            parts=[TextPart(content="partial answer")],
            model_name="test",
            state="interrupted",
        ),
    ]
    assert not is_reusable_run(interrupted)


def test_current_run_filter_excludes_prior_history():
    old = complete_run("old-run")
    current = complete_run("current-run")

    run_id, messages = current_run_messages([*old, *current])

    assert run_id == "current-run"
    assert messages == current


def test_batches_flatten_without_losing_tool_pairs():
    first = complete_run("run-1")
    second = complete_run("run-2")
    assert flatten_batches(
        [MessageBatch(1, first), MessageBatch(2, second)]
    ) == [*first, *second]


def test_ui_projection_is_derived_from_native_types():
    projected = to_ui_messages(
        complete_run(), run_id="run-1", created_at="2026-07-11T00:00:00Z"
    )

    assert [message["role"] for message in projected] == ["user", "assistant"]
    assert projected[1]["content"] == "answer"
