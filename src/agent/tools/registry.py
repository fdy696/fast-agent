"""
工具注册表：name → description → callable。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .schemas import RegisteredTool, ToolFunc


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self, name: str, description: str
    ) -> Callable[[ToolFunc], ToolFunc]:
        def decorator(func: ToolFunc) -> ToolFunc:
            self._tools[name] = RegisteredTool(
                name=name, description=description, func=func
            )
            return func

        return decorator

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    @property
    def all(self) -> list[RegisteredTool]:
        return list(self._tools.values())
