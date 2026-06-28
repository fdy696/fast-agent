import pytest


@pytest.mark.asyncio
async def test_get_me(client, admin_token):
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["username"] == "admin"
    assert "password" not in payload["data"]


@pytest.mark.asyncio
async def test_admin_user_crud(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    create = await client.post(
        "/api/v1/users/",
        headers=headers,
        json={
            "username": "alice",
            "password": "AlicePass123",
            "email": "alice@example.com",
            "is_active": True,
            "is_superuser": False,
        },
    )
    assert create.status_code == 200, create.text
    user_id = create.json()["data"]["id"]

    detail = await client.get(f"/api/v1/users/{user_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["username"] == "alice"

    listing = await client.get("/api/v1/users/", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 2

    update = await client.put(
        f"/api/v1/users/{user_id}",
        headers=headers,
        json={"alias": "Alice", "email": "alice2@example.com"},
    )
    assert update.status_code == 200
    assert update.json()["data"]["alias"] == "Alice"

    delete = await client.delete(f"/api/v1/users/{user_id}", headers=headers)
    assert delete.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_forbidden(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    await client.post(
        "/api/v1/users/",
        headers=headers,
        json={
            "username": "bob",
            "password": "BobPass123",
            "email": "bob@example.com",
            "is_active": True,
            "is_superuser": False,
        },
    )
    login = await client.post("/api/v1/auth/login", json={"username": "bob", "password": "BobPass123"})
    token = login.json()["data"]["access_token"]
    response = await client.get("/api/v1/users/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
