"""
MCP 配置 —— 从 settings 读取 server URLs。
"""

from __future__ import annotations

from core.config import settings

from .types import MCPServerConfig


def discover_mcp_servers() -> list[MCPServerConfig]:
    servers: list[MCPServerConfig] = []
    for name, url, key in [
        ("web_search", settings.MCP_WEB_SEARCH_URL, settings.API_KEY),
        ("amap_maps", settings.MCP_AMAP_MAPS_URL, settings.AMAP_API_KEY or settings.API_KEY),
        ("weather", settings.MCP_WEATHER_URL, settings.API_KEY),
        ("search_image", settings.MCP_SEARCH_IMAGE_URL, settings.API_KEY),
        ("map_crawler", settings.MCP_MAP_CRAWLER_URL, settings.API_KEY),
    ]:
        if url:
            servers.append(
                MCPServerConfig(
                    url=url,
                    api_key=key,
                    name=name,
                    timeout=settings.MCP_TOOL_TIMEOUT,
                )
            )
    return servers
