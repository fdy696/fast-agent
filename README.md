# fastapi-backend-template

轻量 AI 应用后端模板，基于 FastAPI、SQLAlchemy 2.0 Async、Alembic 和 JWT Auth。

## 能力

- FastAPI 应用骨架
- SQLAlchemy 2.0 Async ORM
- Alembic 数据库迁移
- JWT 登录认证与刷新 token
- Swagger Basic Auth
- 用户管理与管理员判断（RESTful users 路由）
- 文件上传与文件映射
- 审计日志写入与查询
- 基础 smoke tests

## 快速开始

推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖（自动创建虚拟环境、安装、锁版本）。

```bash
# 安装 uv（如已安装可跳过）
pip install uv          # 或: pipx install uv / brew install uv / winget install uv

# 一键安装依赖（自动创建 .venv）
uv sync --group dev

# Windows 激活虚拟环境
.venv\Scripts\activate     # cmd
# 或
.\.venv\Scripts\Activate.ps1   # PowerShell

# Linux / macOS 激活虚拟环境
source .venv/bin/activate

# 配置环境变量
cp .env.example .env      # 按需修改 DATABASE_URL 等

# 数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn src:app --reload
```

> 💡 如果没有 PostgreSQL，可将 `.env` 中 `DATABASE_URL` 改为 SQLite：
> ```env
> DATABASE_URL=sqlite+aiosqlite:///./fast_agent.db
> ```

默认管理员配置来自 `.env`：

```env
FIRST_SUPERUSER_USERNAME=admin
FIRST_SUPERUSER_PASSWORD=AdminPass123
```

登录接口：

```text
POST /api/v1/auth/login
GET  /api/v1/users/me
GET  /api/v1/users/
POST /api/v1/users/
PUT  /api/v1/users/{user_id}
DELETE /api/v1/users/{user_id}
```

认证方式：

```http
Authorization: Bearer <access_token>
```

## 测试

```bash
PYTHONPATH=src pytest
```

## 数据库

开发环境可以使用 SQLite：

```env
DATABASE_URL=sqlite+aiosqlite:///./fast_agent.db
```

生产环境建议使用 PostgreSQL：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fast_agent
```
