import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:mysecretpassword@localhost:5432/fast_agent_test",
)
os.environ.setdefault("FIRST_SUPERUSER_USERNAME", "admin")
os.environ.setdefault("FIRST_SUPERUSER_PASSWORD", "AdminPass123")
os.environ.setdefault("FIRST_SUPERUSER_EMAIL", "admin@example.com")
os.environ.setdefault("SWAGGER_UI_USERNAME", "admin")
os.environ.setdefault("SWAGGER_UI_PASSWORD", "admin12345")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-fast-agent-at-least-32-bytes")

from src import app  # noqa: E402

from core.init_app import init_superuser  # noqa: E402
from core.rate_limit import limiter  # noqa: E402
from db.base import Base  # noqa: E402
from db.session import async_engine  # noqa: E402

limiter.enabled = False


@pytest_asyncio.fixture(scope="session")
async def prepare_database():
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except OSError as exc:
        pytest.skip(f"PostgreSQL test database unavailable: {exc}")
    await init_superuser()
    yield
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(prepare_database):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_token(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "AdminPass123"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload["data"]["access_token"]
