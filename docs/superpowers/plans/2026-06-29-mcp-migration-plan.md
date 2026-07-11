# MCP 工具集成 + 统一告警 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 pydantic-ai 2.0 原生 `MCPToolset` 替代 116 行 stub，同时给 MCP + 本地工具加统一失败告警（日志 + 飞书）。

**Architecture:** 3 个新建模块 + 3 个修改文件 + 删除 1 个目录。净 -17 行。

**Tech Stack:** pydantic-ai 2.0, pydantic_ai.mcp.MCPToolset, fastmcp, StreamableHttpTransport, httpx

**Spec:** `docs/superpowers/specs/2026-06-29-mcp-migration-design.md`

## Global Constraints

- pydantic-ai-slim[ag-ui,openai] `==2.0.0`
- fastmcp `>=3.3.1`（已安装）
- 启动严格：任一 MCP server 连接失败 → RuntimeError 崩溃
- 运行时宽容：工具调用失败 → `logger.error` + 飞书 fire-and-forget，由 pydantic-ai 处理 retry
- 代码规范：Karpathy 准则 + 项目 CLAUDE.md
- PYTHONPATH=src 执行所有命令

---

## File Map

```
src/
├── agent/
│   ├── mcp.py              ← NEW  连接管理 + AlertingMCPToolset + 全局单例
│   ├── mcp/                ← DELETE 整个目录（5 文件，116 行 stub）
│   ├── tool_guard.py       ← NEW  make_guarded() 工厂函数
│   ├── model_client.py     ← MODIFY init_agent() 传入 toolsets + 本地工具用 make_guarded
│   └── tools/              ← UNCHANGED
├── utils/
│   └── feishu.py           ← NEW  send_alert() fire-and-forget
├── core/
│   └── init_app.py          ← MODIFY init_data() 换 MCP 初始化调用
└── __init__.py              ← MODIFY lifespan 里换 MCP 关闭调用
```

---

### Task 1: 创建 `src/utils/feishu.py`（独立，零上游依赖）

**Files:**
- Create: `src/utils/feishu.py`

**Interfaces:**
- Produces: `async def send_alert(title: str, content: str) -> None`

- [ ] **Step 1: 创建文件**

```python
"""飞书 Webhook 告警 — fire-and-forget."""

from core.config import settings
from log import logger
import httpx

FEISHU_TIMEOUT = 5


async def send_alert(title: str, content: str) -> None:
    """发送飞书机器人消息。调用方负责 create_task。"""
    url = settings.FEISHU_WEBHOOK_URL
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=FEISHU_TIMEOUT) as client:
            await client.post(url, json={
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"content": title, "tag": "plain_text"}},
                    "elements": [{"tag": "markdown", "content": content}],
                },
            })
    except Exception as e:
        logger.warning(f"飞书告警发送失败: {e}")
```

- [ ] **Step 2: 验证 import**

```bash
PYTHONPATH=src .venv/bin/python -c "from utils.feishu import send_alert; print('OK')"
```

---

### Task 2: 创建 `src/agent/tool_guard.py`（依赖 feishu.py）

**Files:**
- Create: `src/agent/tool_guard.py`

**Interfaces:**
- Consumes: `utils.feishu.send_alert`
- Produces: `make_guarded(name: str, func: Callable) -> Callable`

- [ ] **Step 1: 创建文件**

```python
"""工具调用守护 — 统一的错误日志 + 飞书告警."""

import asyncio
from log import logger


def make_guarded(name: str, func):
    """返回包装后的协程函数，失败时日志 + 飞书告警 + re-raise."""

    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"工具调用失败 [{name}]: {e}")
            from utils.feishu import send_alert
            asyncio.create_task(
                send_alert("工具调用失败", f"工具: {name}\n错误: {e}")
            )
            raise

    wrapper.__name__ = name
    return wrapper
```

- [ ] **Step 2: 验证 import**

```bash
PYTHONPATH=src .venv/bin/python -c "from agent.tool_guard import make_guarded; print('OK')"
```

---

### Task 3: 创建 `src/agent/mcp.py`（依赖 feishu.py，独立于 tool_guard）

**Files:**
- Create: `src/agent/mcp.py`

**Interfaces:**
- Consumes: `utils.feishu.send_alert`
- Produces: `AlertingMCPToolset`、`init_mcp_toolsets() -> None`、`shutdown_mcp_toolsets() -> None`、`get_mcp_toolsets() -> list`

- [ ] **Step 1: 创建文件**

```python
"""MCP 工具集 — pydantic-ai 原生 MCPToolset + 启动管理."""

import asyncio
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport
from core.config import settings
from log import logger

_mcp_toolsets: list = []


class AlertingMCPToolset(MCPToolset):
    """覆写 call_tool：失败时日志 + 飞书告警后 re-raise."""

    async def call_tool(self, name, tool_args, ctx, tool):
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except Exception as e:
            logger.error(f"MCP 工具调用失败 [{name}]: {e}")
            asyncio.create_task(_alert(f"MCP 工具失败", f"{name}: {e}"))
            raise


SERVER_SPECS: list[tuple[str, str, str]] = [
    ("WebSearch",         settings.MCP_WEB_SEARCH_URL,    settings.API_KEY),
    ("amap-maps",         settings.MCP_AMAP_MAPS_URL,     settings.AMAP_API_KEY or settings.API_KEY),
    ("geng-weather",      settings.MCP_WEATHER_URL,       settings.APPCODE),
    ("geng-search-image", settings.MCP_SEARCH_IMAGE_URL,  settings.APPCODE),
    ("geng-map-crawler",  settings.MCP_MAP_CRAWLER_URL,   settings.APPCODE),
]


async def _alert(title: str, content: str) -> None:
    from utils.feishu import send_alert
    try:
        await send_alert(title, content)
    except Exception:
        pass


async def init_mcp_toolsets() -> list:
    """启动时并发连接所有 MCP server，任一失败则崩溃."""
    global _mcp_toolsets
    toolsets = []
    for name, url, api_key in SERVER_SPECS:
        if not url:
            continue
        toolset = AlertingMCPToolset(
            StreamableHttpTransport(url=url),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        toolsets.append(toolset)

    results = await asyncio.gather(
        *[t.__aenter__() for t in toolsets], return_exceptions=True,
    )
    active_specs = [s for s in SERVER_SPECS if s[1]]
    failed_names = []
    for (name, _, _), (toolset, result) in zip(active_specs, zip(toolsets, results)):
        if isinstance(result, Exception):
            logger.error(f"MCP server '{name}' 启动失败: {result}")
            failed_names.append(name)
    if failed_names:
        raise RuntimeError(f"MCP 启动失败: {', '.join(failed_names)}")

    _mcp_toolsets = toolsets
    logger.info(f"MCP: {len(toolsets)} servers initialized")
    return toolsets


async def shutdown_mcp_toolsets() -> None:
    global _mcp_toolsets
    if not _mcp_toolsets:
        return
    await asyncio.gather(
        *[t.__aexit__(None, None, None) for t in _mcp_toolsets],
        return_exceptions=True,
    )
    _mcp_toolsets.clear()


def get_mcp_toolsets() -> list:
    return list(_mcp_toolsets)
```

- [ ] **Step 2: 验证 import**

```bash
PYTHONPATH=src .venv/bin/python -c "from agent.mcp import AlertingMCPToolset, init_mcp_toolsets, shutdown_mcp_toolsets, get_mcp_toolsets; print('OK')"
```

---

### Task 4: 修改 `src/core/init_app.py`（MCP 初始化从 stub 换到新模块）

**Files:**
- Modify: `src/core/init_app.py`

**Interfaces:**
- Consumes: `agent.mcp.init_mcp_toolsets`

- [ ] **Step 1: 替换 init_mcp_servers → init_mcp_toolsets**

Old:
```python
    # Agent: 初始化 MCP 连接 + 扫描技能 + 连接缓存
    from agent.mcp import init_mcp_servers
    await init_mcp_servers()
```

New:
```python
    # Agent: 初始化 MCP 连接（启动时并发预热，任一失败则崩溃）
    from agent.mcp import init_mcp_toolsets
    await init_mcp_toolsets()
```

- [ ] **Step 2: 验证 import 链路**

```bash
PYTHONPATH=src .venv/bin/python -c "from core.init_app import init_data; print('OK')"
```

---

### Task 5: 修改 `src/agent/model_client.py`（Agent 注入 toolsets + 本地工具用 make_guarded）

**Files:**
- Modify: `src/agent/model_client.py`

**Interfaces:**
- Consumes: `agent.mcp.get_mcp_toolsets`、`agent.tool_guard.make_guarded`

- [ ] **Step 1: 添加 import + 修改 init_agent()**

在文件顶部 import 区添加 `from agent.tool_guard import make_guarded` 和 `from agent.mcp import get_mcp_toolsets`，修改 `init_agent()`：

```python
from agent.tool_guard import make_guarded
from agent.mcp import get_mcp_toolsets

# ... (imports unchanged above)

def init_agent() -> Agent:
    """Initialize and cache the process-wide PydanticAI agent."""
    global _agent
    if _agent is not None:
        return _agent

    if not settings.DEEP_SEEK_API_KEY and not settings.API_KEY:
        raise ModelClientError(
            "Agent model is not configured: set DEEP_SEEK_API_KEY or API_KEY."
        )

    client = AsyncOpenAI(
        api_key=settings.DEEP_SEEK_API_KEY or settings.API_KEY,
        base_url=settings.AGENT_BASE_URL,
    )
    model = OpenAIChatModel(
        settings.AGENT_MODEL,
        provider=OpenAIProvider(openai_client=client),
    )
    agent = Agent(model, toolsets=get_mcp_toolsets())
    agent.tool_plain(retries=3)(make_guarded("current_time", current_time))
    agent.tool_plain(retries=2)(make_guarded("ask_human", ask_human))

    _agent = agent
    return agent
```

- [ ] **Step 2: 验证 import**

```bash
PYTHONPATH=src .venv/bin/python -c "from agent.model_client import init_agent; print('OK')"
```

---

### Task 6: 修改 `src/__init__.py`（lifespan 里换 MCP 关闭调用）

**Files:**
- Modify: `src/__init__.py`

**Interfaces:**
- Consumes: `agent.mcp.shutdown_mcp_toolsets`

- [ ] **Step 1: 替换 close_mcp_servers → shutdown_mcp_toolsets**

Old (line 20-21):
```python
    from agent.mcp import close_mcp_servers
    await close_mcp_servers()
```

New:
```python
    from agent.mcp import shutdown_mcp_toolsets
    await shutdown_mcp_toolsets()
```

- [ ] **Step 2: 验证 lifespan 不再引用旧 mcp 模块**

```bash
grep -rn 'close_mcp_servers\|init_mcp_servers' src/ | grep -v __pycache__ | grep -v '.pyc'
# 预期：无输出（所有旧引用已清理）
```

---

### Task 7: 删除 `src/agent/mcp/` 整个目录

**Files:**
- Delete: `src/agent/mcp/__init__.py`
- Delete: `src/agent/mcp/client.py`
- Delete: `src/agent/mcp/config.py`
- Delete: `src/agent/mcp/registry.py`
- Delete: `src/agent/mcp/types.py`
- Delete: `src/agent/mcp/__pycache__/`（如有）

- [ ] **Step 1: 删除目录**

```bash
rm -rf src/agent/mcp/
```

- [ ] **Step 2: 确认无残留引用**

```bash
grep -rn 'from agent.mcp import\|from agent.mcp\.' src/ | grep -v __pycache__
# 预期：仅新文件 src/agent/mcp.py 自身有有效引用（get_mcp_toolsets 等），无旧引用
```

- [ ] **Step 3: 全量 import 验证**

```bash
PYTHONPATH=src .venv/bin/python -c "
from agent.mcp import init_mcp_toolsets, shutdown_mcp_toolsets, get_mcp_toolsets, AlertingMCPToolset
from agent.tool_guard import make_guarded
from agent.model_client import init_agent, get_agent, call_compress_model
from utils.feishu import send_alert
from core.init_app import init_data
print('All imports OK')
"
```

---

### Task 8: 运行测试套件

- [ ] **Step 1: 运行全部测试**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q \
  --ignore=tests/test_agent_loop.py \
  --ignore=tests/test_tools.py
```
预期：15 passed, 1 skipped（与改动前一致）

---

## 任务总结

| Task | 文件 | 操作 | 依赖 |
|------|------|------|------|
| 1 | `src/utils/feishu.py` | 新建 | 无 |
| 2 | `src/agent/tool_guard.py` | 新建 | Task 1 |
| 3 | `src/agent/mcp.py` | 新建 | Task 1 |
| 4 | `src/core/init_app.py` | 修改 | Task 3 |
| 5 | `src/agent/model_client.py` | 修改 | Task 2, 3 |
| 6 | `src/__init__.py` | 修改 | Task 3 |
| 7 | `src/agent/mcp/` | 删除 | Task 3, 4, 6 |
| 8 | `tests/` | 验证 | All |
