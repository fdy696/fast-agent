import pytest


@pytest.mark.asyncio
async def test_login_success(client):
    response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "AdminPass123"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_login_failed(client):
    response = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert response.status_code in {400, 401}


@pytest.mark.asyncio
async def test_current_user(client, admin_token):
    response = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["data"]["username"] == "admin"
