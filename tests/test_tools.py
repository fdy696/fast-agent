from agent.tools import ask_human, current_time


async def test_current_time_returns_string():
    result = await current_time()
    assert isinstance(result, str)
    assert "T" in result  # ISO 8601


async def test_ask_human_returns_handoff_payload():
    result = await ask_human(
        question="Which city should I use?",
        context="The user mentioned a trip but did not provide a destination.",
        options=["Shanghai", "Beijing"],
    )

    assert result == {
        "type": "human_input_required",
        "question": "Which city should I use?",
        "context": "The user mentioned a trip but did not provide a destination.",
        "options": ["Shanghai", "Beijing"],
        "status": "pending",
    }


async def test_ask_human_filters_empty_options():
    result = await ask_human(
        question="ok?",
        options=["valid", "", None],
    )

    assert result["options"] == ["valid"]


async def test_ask_human_handles_none_options():
    result = await ask_human(question="just asking")
    assert result["options"] == []
    assert result["context"] is None
    assert result["status"] == "pending"
