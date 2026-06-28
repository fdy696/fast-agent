"""PydanticAI glue for registered tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.types import AgentEvent, ToolDefinition

from .executor import ToolExecutor


ToolEventSink = Callable[[AgentEvent], None]


class ToolAdapter:
    @staticmethod
    def to_pydantic_tools(
        executor: ToolExecutor,
        tools: list[ToolDefinition],
        event_sink: ToolEventSink | None = None,
    ) -> list[Any]:
        try:
            from pydantic_ai import Tool
        except ImportError as exc:
            raise RuntimeError("PydanticAI is not installed.") from exc

        return [
            Tool(
                _build_tool_func(executor, tool, event_sink),
                name=tool.name,
                description=tool.description,
                takes_ctx=False,
            )
            for tool in tools
        ]


def _build_tool_func(
    executor: ToolExecutor,
    tool: ToolDefinition,
    event_sink: ToolEventSink | None,
):
    async def run_tool(**kwargs: Any) -> Any:
        if event_sink:
            event_sink(AgentEvent("tool.call", {"name": tool.name, "args": kwargs}))

        result = await executor.execute(tool.name, kwargs)
        data: dict[str, Any] = {"name": tool.name, "ok": result.ok}
        if result.ok:
            data["data"] = result.data
        else:
            data["error"] = result.error

        if event_sink:
            event_sink(AgentEvent("tool.result", data))

        if not result.ok:
            return {"error": result.error}
        return result.data

    return run_tool
