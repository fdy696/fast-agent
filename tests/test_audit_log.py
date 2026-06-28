import pytest


@pytest.mark.asyncio
async def test_audit_log_list_admin_only(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.post("/api/v1/users/", headers=headers, json={"username": "charlie", "password": "Charlie123", "email": "charlie@example.com", "is_active": True, "is_superuser": False})
    response = await client.get("/api/v1/auditlog/", headers=headers)
    assert response.status_code == 200
    assert "data" in response.json()
