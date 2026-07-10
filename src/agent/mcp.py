"""MCP 工具集 — pydantic-ai 原生 MCPToolset + 启动管理."""

import asyncio
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport
from core.config import settings
from log import logger

_mcp_toolsets: list = []


async def _alert(title: str, content: str) -> None:
    from utils.queue import queue
    await queue.enqueue("send_feishu_alert", title=title, content=content)


class AlertingMCPToolset(MCPToolset):

    async def call_tool(self, name, tool_args, ctx, tool):
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except Exception as e:
            if "not connected" in str(e).lower():
                logger.warning(f"MCP session died [{name}], reconnecting...")
                await self._reconnect()
                try:
                    return await super().call_tool(name, tool_args, ctx, tool)
                except Exception as e2:
                    logger.error(f"MCP 工具调用失败 [{name}] (retry): {e2}")
                    asyncio.create_task(_alert(f"MCP 工具失败 (retry)", f"{name}: {e2}"))
                    raise
            logger.error(f"MCP 工具调用失败 [{name}]: {e}")
            asyncio.create_task(_alert(f"MCP 工具失败", f"{name}: {e}"))
            raise

    async def _reconnect(self) -> None:
        count = self._running_count
        for _ in range(count):
            try:
                await super().__aexit__(None, None, None)
            except Exception:
                pass
        for _ in range(count):
            await super().__aenter__()
        logger.info(f"MCP reconnected, running_count={self._running_count}")


SERVER_SPECS: list[tuple[str, str, str]] = [
    ("WebSearch",         settings.MCP_WEB_SEARCH_URL,    settings.API_KEY),
    ("amap-maps",         settings.MCP_AMAP_MAPS_URL,     settings.AMAP_API_KEY or settings.API_KEY),
    ("image-search",      settings.MCP_SEARCH_IMAGE_URL,  settings.APPCODE),
]


async def init_mcp_toolsets() -> list:
    """启动时并发连接所有 MCP server，任一失败则崩溃."""
    global _mcp_toolsets
    toolsets = []
    for name, url, api_key in SERVER_SPECS:
        if not url:
            continue
        toolset = AlertingMCPToolset(
            StreamableHttpTransport(url=url, headers={"Authorization": f"Bearer {api_key}"}),
        )
        toolsets.append(toolset)

    async def _connect(toolset, name):
        for attempt in range(1, 4):
            try:
                return await toolset.__aenter__()
            except Exception as e:
                if attempt == 3:
                    logger.error(f"MCP server '{name}' failed after 3 attempts: {e}")
                    raise
                wait = 2 ** attempt
                logger.warning(f"MCP server '{name}' attempt {attempt} failed, retrying in {wait}s: {e}")
                await asyncio.sleep(wait)

    results = await asyncio.gather(
        *[_connect(t, name) for (name, _, _), t in zip(SERVER_SPECS, toolsets)],
        return_exceptions=True,
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
