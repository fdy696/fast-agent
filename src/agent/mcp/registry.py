"""
MCP 工具注册表 —— 从 MCP server 配置生成 Tool 列表。
"""

from __future__ import annotations

from agent.tools.schemas import RegisteredTool
from .config import discover_mcp_servers
from .types import MCPServerConfig


class MCPToolRegistry:
    """把 MCP server 暴露为工具列表，供 ToolExecutor 统一消费。"""

    def __init__(self) -> None:
        self._servers: list[MCPServerConfig] = []

    def discover(self) -> None:
        self._servers = discover_mcp_servers()

    @property
    def servers(self) -> list[MCPServerConfig]:
        return list(self._servers)

    def as_tools(self) -> list[RegisteredTool]:
        """当前阶段返回静态描述，后续接入 fastmcp Client 后改动态调用。"""
        tools: list[RegisteredTool] = []
        for srv in self._servers:
            def _mcp_call(name=srv.name):
                # TODO: 通过 fastmcp Client.call_tool 实际调用
                return f"[MCP {name}] stub"
            tools.append(
                RegisteredTool(
                    name=srv.name,
                    description=f"MCP server: {srv.name} ({srv.url})",
                    func=_mcp_call,
                )
            )
        return tools
