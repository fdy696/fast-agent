"""Tool execution only. Framework adapters live elsewhere."""

from __future__ import annotations

import inspect
from typing import Any

from agent.types import ToolDefinition, ToolResult

from .registry import ToolRegistry
from .schemas import RegisteredTool


class ToolExecutor:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()
        self.history: list[dict[str, Any]] = []

    @property
    def tools(self) -> list[RegisteredTool]:
        return self.registry.all

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(name=tool.name, description=tool.description)
            for tool in self.tools
        ]

    async def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        args = args or {}
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"Unknown tool: {name}")

        try:
            result = tool.func(**args)
            if inspect.isawaitable(result):
                result = await result
            self.history.append({"name": name, "args": args, "result": result})
            return ToolResult(ok=True, data=result)
        except Exception as exc:
            self.history.append({"name": name, "args": args, "error": str(exc)})
            return ToolResult(ok=False, error=str(exc))
