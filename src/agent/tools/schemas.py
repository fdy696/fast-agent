"""
工具共享类型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolFunc = Callable[..., Any]


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    func: ToolFunc
