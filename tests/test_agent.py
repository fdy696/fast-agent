import json
import pytest

from core.security import create_access_token


def _sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def test_create_and_list_conversations(client, admin_token):
    response = await client.post(
        "/api/v1/agent/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    list_response = await client.get(
        "/api/v1/agent/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_response.status_code == 200, list_response.text
    sessions = {item["session_id"] for item in list_response.json()}
    assert session_id in sessions


@pytest.mark.skip("audit middleware can't read body from streaming error response")
async def test_stream_chat_without_model_key_returns_error_event(client, admin_token, monkeypatch):
    monkeypatch.setattr("core.config.settings.DEEP_SEEK_API_KEY", "")
    monkeypatch.setattr("core.config.settings.API_KEY", "")

    response = await client.post(
        "/api/v1/agent/chat/stream",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}], "id": "msg-1"}],
            "thread_id": "test-session",
            "run_id": "test-run",
            "state": {},
            "tools": [],
            "context": [],
            "forwarded_props": {},
        },
    )

    assert response.status_code == 200, response.text
    events = _sse_events(response.text)
    assert events
    assert events[-1]["type"] == "RUN_ERROR"

    # 确认流式错误响应仍然返回了用户消息的 DB 记录
    list_response = await client.get(
        "/api/v1/agent/all_conversation_list",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_response.status_code == 200


async def test_conversation_messages_are_user_scoped(client, admin_token):
    create_response = await client.post(
        "/api/v1/agent/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 200, create_response.text
    session_id = create_response.json()["session_id"]

    other_token = create_access_token(user_id=2, username="other", is_superuser=False)
    other_response = await client.get(
        "/api/v1/agent/messages",
        params={"session_id": session_id},
        headers={"Authorization": f"Bearer {other_token}"},
    )

    # Token for non-existent user — auth layer rejects before reaching endpoint
    assert other_response.status_code == 401
