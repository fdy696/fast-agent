"""
内置纯函数工具 —— current_time 等。
"""

from datetime import UTC, datetime

from agent.tools.registry import ToolRegistry
from agent.tools.schemas import RegisteredTool


def register_builtin_tools(registry: ToolRegistry) -> list[RegisteredTool]:

    @registry.register(
        name="current_time",
        description="Return the current UTC time as an ISO 8601 string.",
    )
    def current_time() -> str:
        return datetime.now(UTC).isoformat()

    return registry.all
