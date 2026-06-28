import base64

import pytest


def test_import_src_app():
    """smoke test: from src import app 不抛循环导入异常"""
    from src import app  # noqa: F811
    assert app is not None
    assert app.title == "fast-agent"


def test_import_create_app():
    """smoke test: from src import create_app 工厂函数可用"""
    from src import create_app
    assert create_app is not None


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/api/v1/base/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_docs_basic_auth(client):
    token = base64.b64encode(b"admin:admin12345").decode()
    response = await client.get("/docs", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 200
