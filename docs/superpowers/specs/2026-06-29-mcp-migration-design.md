# MCP 工具集成 + 统一告警 — 设计文档

> 日期：2026-06-29 | 状态：已确认

## 1. 概述

用 pydantic-ai 2.0 内置 `MCPToolset` 替代当前 `src/agent/mcp/` 下 116 行 stub 代码。同时统一 MCP 工具和本地工具的错误处理，在失败时写日志 + 飞书告警。

## 2. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 启动策略 | **启动时预连接** | FastAPI + pydantic-ai 生产标准姿态 |
| 启动校验 | **严格** | 任一 MCP server 连不上 → 应用崩溃 |
| 运行时容错 | **宽容** | 工具调用失败 → 日志 + 飞书告警，pydantic-ai 自动 retry/通知 LLM |
| 生命周期管理 | **FastAPI lifespan** | 全局单例，`init_agent()` 纯取用 |
| 错误捕获机制 | **子类覆写 + make_guarded** | `AlertingMCPToolset` 覆写 `call_tool()`，`make_guarded()` 工厂包装本地工具 |
| 飞书告警 | **新建独立模块** | 从 `FEISHU_WEBHOOK_URL` 读取配置，fire-and-forget |

## 3. 架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI lifespan                                                    │
│                                                                      │
│  startup:                                                            │
│    init_mcp_toolsets()                                               │
│      → 5 × AlertingMCPToolset(url, transport)                        │
│      → await asyncio.gather(*[t.__aenter__()])  ← 并发预热连接       │
│      → 任一失败 → MCPError → 应用崩溃                                 │
│      → 写入全局 _mcp_toolsets                                        │
│                                                                      │
│  shutdown:                                                           │
│    shutdown_mcp_toolsets()                                           │
│      → await asyncio.gather(*[t.__aexit__()])                        │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  init_agent()                         ← 纯取用者，不管理生命周期        │
│    agent = Agent(model, toolsets=_mcp_toolsets)                      │
│    agent.tool_plain(retries=3)(make_guarded("current_time", current_time))│
│    agent.tool_plain(retries=2)(make_guarded("ask_human", ask_human))    │
└──────────────────────────────────────────────────────────────────────┘
```

### 运行时错误流

```
LLM 调用 tool
  │
  ├─ MCP 工具 → AlertingMCPToolset.call_tool()
  │     ├─ 成功 → 返回结果
  │     └─ 失败 → logger.error + send_feishu_alert() + raise
  │               → pydantic-ai 根据 tool_error_behavior='retry' 处理
  │
  └─ 本地工具 → make_guarded 返回的 wrapper 函数
        ├─ 成功 → 返回结果
        └─ 失败 → logger.error + send_feishu_alert() + raise
                  → pydantic-ai 根据 retries 参数处理
```

## 4. 模块设计

### 4.1 `src/agent/mcp.py`（新建，约 60 行）

三个职责：连接管理 + 子类覆写 + 全局单例。

```python
"""MCP 工具集 — pydantic-ai 原生 MCPToolset + 启动管理."""

import asyncio
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport
from core.config import settings
from log import logger

_mcp_toolsets: list[MCPToolset] = []


class AlertingMCPToolset(MCPToolset):
    """覆写 call_tool：失败时日志 + 飞书告警后 re-raise.

    pydantic-ai 2.0 的 call_tool 签名为 call_tool(name, tool_args, ctx, tool).
    """

    async def call_tool(self, name, tool_args, ctx, tool):
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except Exception as e:
            logger.error(f"MCP 工具调用失败 [{name}]: {e}")
            asyncio.create_task(_alert(f"MCP 工具失败", f"{name}: {e}"))
            raise


SERVER_SPECS: list[tuple[str, str, str]] = [
    ("WebSearch",       settings.MCP_WEB_SEARCH_URL,   settings.API_KEY),
    ("amap-maps",       settings.MCP_AMAP_MAPS_URL,    settings.AMAP_API_KEY or settings.API_KEY),
    ("geng-weather",    settings.MCP_WEATHER_URL,       settings.APPCODE),
    ("geng-search-image", settings.MCP_SEARCH_IMAGE_URL, settings.APPCODE),
    ("geng-map-crawler",  settings.MCP_MAP_CRAWLER_URL,  settings.APPCODE),
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
    failed = [(name, exc) for (name, _, _), (_, exc) in
              zip([s for s in SERVER_SPECS if s[1]], zip(toolsets, results))
              if isinstance(exc, Exception)]
    if failed:
        for name, exc in failed:
            logger.error(f"MCP server '{name}' 启动失败: {exc}")
        raise RuntimeError(f"MCP 启动失败: {', '.join(n for n, _ in failed)}")

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

### 4.2 `src/agent/tool_guard.py`（新建，约 20 行）

```python
"""工具调用守护 — 统一的错误日志 + 飞书告警."""

import asyncio
from log import logger


def make_guarded(name: str, func):
    """返回一个包装后的协程函数，失败时日志 + 飞书告警 + re-raise."""

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

### 4.3 `src/utils/feishu.py`（新建，约 20 行）

```python
"""飞书 Webhook 告警."""

import httpx
from core.config import settings
from log import logger

FEISHU_TIMEOUT = 5  # 秒


async def send_alert(title: str, content: str) -> None:
    """发送飞书机器人消息（fire-and-forget 调用方自行 create_task）."""
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

### 4.4 `src/agent/model_client.py`（修改，约 +5 行）

```python
from agent.tool_guard import make_guarded
from agent.mcp import get_mcp_toolsets

def init_agent() -> Agent:
    ...
    agent = Agent(model, toolsets=get_mcp_toolsets())
    agent.tool_plain(retries=3)(make_guarded("current_time", current_time))
    agent.tool_plain(retries=2)(make_guarded("ask_human", ask_human))
    ...
```

### 4.5 `src/__init__.py`（修改，约 -2 行）

lifespan startup 里替换 MCP 初始化调用：

```python
# 旧：from agent.mcp import init_mcp_servers; await init_mcp_servers()
# 新：
from agent.mcp import init_mcp_toolsets
await init_mcp_toolsets()
```

lifespan shutdown 里替换 MCP 关闭调用：

```python
# 旧：from agent.mcp import close_mcp_servers; await close_mcp_servers()
# 新：
from agent.mcp import shutdown_mcp_toolsets
await shutdown_mcp_toolsets()
```

## 5. 不改动的部分

| 组件 | 原因 |
|------|------|
| `agent/tools/` (date_tool, human_tool) | 本地工具实现保留，`init_agent` 里用 `make_guarded()` 一行包装 |
| `agent/skills/` | 独立系统 |
| `agent/prompts.py` | 包含 `generate_title()` 和 `TITLE_PROMPT`，不涉及 |
| `services/agent_service.py` | 不碰，`stream_ag_ui()` 继续用 `get_agent()` |
| `core/config.py` MCP URL | 引用方式不变 |

## 6. 文件变更汇总

| 文件 | 操作 | 行数 |
|------|------|------|
| `src/agent/mcp.py` | **新建** | +60 |
| `src/agent/tool_guard.py` | **新建** | +20 |
| `src/utils/feishu.py` | **新建** | +20 |
| `src/agent/model_client.py` | 修改 | +5 |
| `src/__init__.py` | 修改 | +2/-2 |
| `src/agent/mcp/__init__.py` | **删除** | -6 |
| `src/agent/mcp/client.py` | **删除** | -31 |
| `src/agent/mcp/config.py` | **删除** | -30 |
| `src/agent/mcp/registry.py` | **删除** | -39 |
| `src/agent/mcp/types.py` | **删除** | -16 |
| **净效果** | | **-17 行** |

## 7. Spec 自查

- [x] 无 TBD / TODO / 占位符
- [x] 内部一致：启动链路、运行时错误流、关闭链路无矛盾
- [x] 范围聚焦：MCP + 工具告警，无功能蔓延
- [x] 无歧义：每个模块的职责和 API 明确定义
