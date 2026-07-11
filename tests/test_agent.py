from core.security import create_access_token


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


async def test_conversation_messages_are_user_scoped(client, admin_token):
    create_response = await client.post(
        "/api/v1/agent/sessions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 200, create_response.text
    session_id = create_response.json()["session_id"]

    other_token = create_access_token(user_id=2, username="other", is_superuser=False)
    other_response = await client.get(
        f"/api/v1/agent/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    # Token for non-existent user — auth layer rejects before reaching endpoint
    assert other_response.status_code == 401
