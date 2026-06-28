import pytest


@pytest.mark.asyncio
async def test_file_upload_and_list(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["file_id"]

    listing = await client.get("/api/v1/files/", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()["data"]) >= 1
