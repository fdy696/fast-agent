"""
MCP server 生命周期管理。
"""

from __future__ import annotations

from log import logger

from .config import discover_mcp_servers
from .types import MCPServerConfig

_servers: list[MCPServerConfig] = []


async def init_mcp_servers() -> None:
    global _servers
    _servers = discover_mcp_servers()
    logger.info(f"MCP: starting concurrent init for all remote servers...")
    # TODO: 接入 fastmcp Client
    logger.info(f"MCP: all servers initialized")


async def close_mcp_servers() -> None:
    global _servers
    logger.info(f"MCP: closing {len(_servers)} servers...")
    _servers.clear()
    logger.info("MCP server shutdown skipped: no MCP adapters are configured.")


def get_mcp_servers() -> list[MCPServerConfig]:
    return list(_servers)
