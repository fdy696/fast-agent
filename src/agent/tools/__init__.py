from .adapter import ToolAdapter
from .executor import ToolExecutor
from .registry import ToolRegistry
from .schemas import RegisteredTool, ToolFunc
from .builtin import register_builtin_tools


def create_default_tool_executor() -> ToolExecutor:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return ToolExecutor(registry=registry)


__all__ = [
    "ToolAdapter",
    "ToolExecutor",
    "ToolRegistry",
    "RegisteredTool",
    "ToolFunc",
    "create_default_tool_executor",
]
