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
- PydanticAI 原生消息持久化、SSE 流式输出与失败重试
- 累计摘要 + 最近完整 Turn 的 Token 受控历史

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

# 启动数据库和缓存
docker compose up -d

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

Agent 接口：

```text
POST /api/v1/agent/chat/completions
POST /api/v1/agent/chat/stream
POST /api/v1/agent/chat/messages/{message_id}/retry
GET  /api/v1/agent/get_conversation?session_id=...
```

历史消息完整保存在数据库中；只有 `completed` 且包含原生
PydanticAI `message_data` 的消息会进入模型上下文。长会话自动使用
“累计摘要 + 最近完整 Turn”，不会拆开工具调用与工具返回。

认证方式：

```http
Authorization: Bearer <access_token>
```

## 测试

```bash
PYTHONPATH=src pytest
```

## 基础设施容器（Docker）

项目依赖 **PostgreSQL**（pgvector）和 **Redis**，通过 Docker Compose 管理：

```bash
# 启动所有服务
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止所有服务
docker compose down
```

> ⚠️ 如果拉取镜像超时，可能是系统代理未配置到 Docker 守护进程，运行以下命令：
> ```bash
> sudo mkdir -p /etc/systemd/system/docker.service.d
> ```
> 然后创建 `/etc/systemd/system/docker.service.d/proxy.conf`：
> ```ini
> [Service]
> Environment="HTTP_PROXY=http://127.0.0.1:7897"
> Environment="HTTPS_PROXY=http://127.0.0.1:7897"
> ```
> 最后重启 Docker：`sudo systemctl daemon-reload && sudo systemctl restart docker`

## 数据库

开发环境可以使用 SQLite：

```env
DATABASE_URL=sqlite+aiosqlite:///./fast_agent.db
```

生产环境建议使用 PostgreSQL：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fast_agent
```
