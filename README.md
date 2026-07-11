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
uvicorn src:app --reload --port 6000
```

本项目仅支持 PostgreSQL；本地开发与生产使用同一数据库能力集合。

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
POST /api/v1/agent/sessions
GET  /api/v1/agent/sessions
POST /api/v1/agent/sessions/{session_id}/archive
POST /api/v1/agent/sessions/{session_id}/delete
POST /api/v1/agent/sessions/{session_id}/messages
GET  /api/v1/agent/sessions/{session_id}/messages
```

后端不预存用户输入；前端在当前页面内乐观展示。一次 Agent Run 完成或
中断后，后端将完整的 PydanticAI `new_messages()` 作为一条 JSONB 记录保存。
只有语义完整的 Run 会进入下一轮上下文和滚动摘要。

认证方式：

```http
Authorization: Bearer <access_token>
```

## 测试

```bash
uv run --extra dev pytest
```

## 基础设施容器（Docker）

项目依赖 **PostgreSQL**（pgvector）和 **Redis**，全栈通过 Docker Compose 管理：

```bash
# 一键启动全部服务（Web + Worker + PostgreSQL + Redis）
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止所有服务
docker compose down
```

### 容器清单

| 服务 | 容器 | 端口 | 说明 |
|------|------|------|------|
| Frontend | `fast-agent-frontend` | 3000 | Vue 3 + Vant 移动端前端 |
| Web | `fast-agent-web` | 6000 | FastAPI 后端 |
| Worker | `fast-agent-worker` | — | SAQ 任务队列 Worker |
| PostgreSQL | `fast-agent-postgres` | 5432 | 主数据库（pgvector） |
| Redis | `fast-agent-redis` | 6379 | 缓存 + SAQ 消息队列 |
| Milvus | `milvus-standalone` | 19530 | 向量数据库 |
| MinIO | `milvus-minio` | 9000 | Milvus 对象存储 |
| Attu | `milvus-attu` | 8000 | Milvus 管理面板 |

## 启动方式

### 开发环境（热重载）

代码修改实时生效，基础设施走 Docker：

```bash
# Linux / macOS 激活虚拟环境
source .venv/bin/activate

# 终端 1：基础设施
docker compose up -d postgres redis

# 终端 2：后端（--reload 热重载）
uvicorn src:app --reload --port 6000

# 终端 3：Worker
PYTHONPATH=src .venv/bin/python run_worker.py

# 前端（在 web/ 目录下）
cd ../web && pnpm run dev      # localhost:3000
```

### 生产环境

全栈 Docker，无热重载：

```bash
# 数据库迁移（首次或 schema 变更时）
docker compose run --rm web alembic upgrade head

# 启动全部服务
docker compose up -d
```

### 任务队列

SAQ Worker 当前用于发送飞书告警；滚动摘要在流式响应结束后以最佳努力方式执行。

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

开发和生产环境统一使用 PostgreSQL：

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/fast_agent
```

示例：

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fast_agent
```
