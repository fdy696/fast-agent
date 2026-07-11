# MCP 工具集成迁移计划（stub → pydantic-ai 原生）

## 0. 当前状态

`src/agent/mcp/` 下 4 个文件 116 行，全是 stub 占位：

| 文件 | 状态 |
|------|------|
| `client.py` | `TODO: 接入 fastmcp Client`，只打印日志 |
| `config.py` | 从 settings 读 URL → `MCPServerConfig` dict |
| `registry.py` | `return f"[MCP {name}] stub"` |
| `types.py` | `MCPServerConfig` dataclass |

`model_client.py` 的 `init_agent()` 里没有注册任何 MCP 工具。

## 1. 目标

用 pydantic-ai 1.0 内置的 `MCPToolset` 替代手写的 MCP 层。5 个 MCP server 的工具自动发现、注册、调用。

### pydantic-ai 1.0 的关键 API

```python
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport

# 每个 MCP server 一个 MCPToolset
toolset = MCPToolset(
    transport=StreamableHttpTransport(url="...", headers={"Authorization": "Bearer ..."}),
)

# Agent 创建时注入
agent = Agent(model, toolsets=[toolset1, toolset2, ...])
```

- `MCPToolset` 内部用 `fastmcp.Client` 管理连接
- 工具 schema 自动从 MCP server 拉取
- Agent 运行时自动分发 tool call
- `load_mcp_toolsets()` 可从 JSON 配置文件批量加载

> 项目已安装 `fastmcp>=3.3.1`，不需要新依赖。

## 2. 改动清单

### 2.1 新建：`src/agent/mcp.py`（单文件，约 30 行）

```python
"""MCP 工具集 —— 基于 pydantic-ai 原生 MCPToolset."""
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport
from core.config import settings


def build_mcp_toolsets() -> list:
    servers = [
        ("WebSearch", settings.MCP_WEB_SEARCH_URL, settings.API_KEY),
        ("amap-maps", settings.MCP_AMAP_MAPS_URL, settings.AMAP_API_KEY or settings.API_KEY),
        ("geng-weather", settings.MCP_WEATHER_URL, settings.APPCODE),
        ("geng-search-image", settings.MCP_SEARCH_IMAGE_URL, settings.APPCODE),
        ("geng-map-crawler", settings.MCP_MAP_CRAWLER_URL, settings.APPCODE),
    ]
    toolsets = []
    for name, url, api_key in servers:
        if not url:
            continue
        transport = StreamableHttpTransport(
            url=url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        toolsets.append(MCPToolset(transport=transport))
    return toolsets
```

### 2.2 修改：`src/agent/model_client.py`

`init_agent()` 调用 `build_mcp_toolsets()` 并传给 `Agent()`：

```python
from agent.mcp import build_mcp_toolsets

def init_agent() -> Agent:
    ...
    agent = Agent(model, toolsets=build_mcp_toolsets())
    agent.tool_plain(retries=3)(current_time)
    ...
    return agent
```

### 2.3 删除：`src/agent/mcp/` 整个目录

| 删除文件 | 原因 |
|----------|------|
| `mcp/client.py` (31 行) | stub，被 MCPToolset 替代 |
| `mcp/config.py` (30 行) | URL→config 逻辑内联到 `mcp.py` |
| `mcp/registry.py` (39 行) | stub，Agent 自动管理 |
| `mcp/types.py` (16 行) | `MCPServerConfig` 不再需要 |
| `mcp/__init__.py` | 目录删除 |

### 2.4 清理引用

| 文件 | 改动 |
|------|------|
| `src/__init__.py` | 如果 `init_app` 引用了 `mcp/client.py` 的 `init_mcp_servers`，删掉调用 |
| `src/core/init_app.py` | 同上 |

---

## 3. 数据流对比

### 迁移前（stub）

```
init_agent()
  → tools = [current_time, ask_human]  ← MCP 工具不存在
  → Agent 不知道任何 MCP server

Agent 调用 tool → 返回 "[MCP web_search] stub"  ← 假数据
```

### 迁移后

```
init_agent()
  → build_mcp_toolsets()
    → 为每个 MCP server 创建 MCPToolset
  → Agent(model, toolsets=[...MCP toolsets...])
  → Agent 内部通过 fastmcp.Client 连接所有 server
  → 拉取工具 schema → 自动注册到 Agent

Agent 调用 tool → MCPToolset.call_tool() → fastmcp.Client → 真实结果
```

---

## 4. 不改动的部分

| 组件 | 状态 | 原因 |
|------|------|------|
| `agent/tools/` (date_tool, human_tool) | 保留 | 本地工具，继续用 `agent.tool_plain()` 注册 |
| `agent/skills/` | 保留 | skill 系统独立于 MCP |
| `call_compress_model()` | 保留 | 标题生成用，不是 MCP 工具 |
| `core/config.py` 的 MCP URL | 保留 | 引用方式不变 |

---

## 5. 改动文件清单

| 文件 | 改动 | 行数变化 |
|------|------|----------|
| `src/agent/mcp.py` | **新建** | +35 |
| `src/agent/model_client.py` | 修改 `init_agent()` | +3 |
| `src/agent/mcp/__init__.py` | **删除** | -6 |
| `src/agent/mcp/client.py` | **删除** | -31 |
| `src/agent/mcp/config.py` | **删除** | -30 |
| `src/agent/mcp/registry.py` | **删除** | -39 |
| `src/agent/mcp/types.py` | **删除** | -16 |
| 引用清理 | 修改 | -5 |
| **合计** | | **-89 行** |
