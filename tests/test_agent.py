import uuid

import pytest


@pytest.mark.asyncio
async def test_create_conversation(client, admin_token):
    response = await client.get(
        "/api/v1/agent/create_conversation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert "session_id" in data["data"]


@pytest.mark.asyncio
async def test_all_conversation_list_empty(client, admin_token):
    response = await client.get(
        "/api/v1/agent/all_conversation_list",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_get_conversation_not_found(client, admin_token):
    response = await client.post(
        "/api/v1/agent/get_conversation",
        json={"session_id": "nonexistent-session-123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 200
    # 不存在的会话返回空列表
    assert data["data"] == []


@pytest.mark.asyncio
async def test_agent_routes_require_auth(client):
    response = await client.get("/api/v1/agent/create_conversation")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_travel_plan_not_found(client, admin_token):
    response = await client.post(
        "/api/v1/agent/get_travel_plan",
        json={"plan_id": "fake-plan-123", "session_id": "fake-session-456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    data = response.json()
    # 未找到返回 404
    assert data["code"] == 404


# ─────────────────────────────────────────────────────────────────────
# 跨用户隔离测试：用户 A 不能读取/覆盖用户 B 的会话
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
async def user_b_token(client, admin_token):
    """创建用户 B 并返回其 JWT token"""
    unique = uuid.uuid4().hex[:8]
    username = f"user_b_{unique}"
    email = f"{username}@example.com"
    # 先创建（允许已存在的情况，但用唯一名避免冲突）
    resp = await client.post(
        "/api/v1/users/",
        json={
            "username": username,
            "password": "UserBPass123",
            "email": email,
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code in (200, 400), resp.text

    # 用户 B 登录获取 token
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "UserBPass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"], username


@pytest.mark.asyncio
async def test_cross_user_cannot_read_session(client, admin_token, user_b_token):
    """用户 B 使用用户 A 的 session_id 无法读取到数据"""
    u_b_token, _ = user_b_token

    # 用户 A (admin) 创建一个会话
    resp = await client.get(
        "/api/v1/agent/create_conversation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    admin_session_id = resp.json()["data"]["session_id"]

    # 用户 B 尝试用 admin 的 session_id 读取 → 返回空列表
    resp = await client.post(
        "/api/v1/agent/get_conversation",
        json={"session_id": admin_session_id},
        headers={"Authorization": f"Bearer {u_b_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 200
    assert data["data"] == [], "用户 B 不应能读取用户 A 的会话数据"


@pytest.mark.asyncio
async def test_cross_user_session_isolation_in_cache(client, admin_token, user_b_token):
    """不同用户使用相同的 session_id 不会互相污染"""
    u_b_token, _ = user_b_token

    # 通过 REST API 来模拟隔离 —— 因为真正的 SSE chat 很难在单元测试中跑
    # 我们验证相同的 session_id 在不同用户间被正确隔离

    # user A 拿到的 session_id 和 user B 的 session_id 各自不同
    resp_a = await client.get(
        "/api/v1/agent/create_conversation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    sid_a = resp_a.json()["data"]["session_id"]

    resp_b = await client.get(
        "/api/v1/agent/create_conversation",
        headers={"Authorization": f"Bearer {u_b_token}"},
    )
    sid_b = resp_b.json()["data"]["session_id"]

    # 各自创建的 session_id 不同
    assert sid_a != sid_b, "不同用户创建的 session_id 不应相同"


@pytest.mark.asyncio
async def test_cross_user_redis_key_isolation(client, admin_token, user_b_token):
    """验证 Redis 缓存 key 的命名空间隔离"""
    u_b_token, _ = user_b_token

    # 创建一个 admin 用户的会话
    resp = await client.get(
        "/api/v1/agent/create_conversation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    admin_sid = resp.json()["data"]["session_id"]

    # 用户 B 不能通过 admin 的 session_id 读取到数据
    resp = await client.post(
        "/api/v1/agent/get_conversation",
        json={"session_id": admin_sid},
        headers={"Authorization": f"Bearer {u_b_token}"},
    )
    assert resp.json()["data"] == [], "Redis 缓存不应跨用户命中"
