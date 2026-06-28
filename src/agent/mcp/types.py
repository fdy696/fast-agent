"""
MCP 共享类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MCPServerConfig:
    url: str
    api_key: str = ""
    name: str = ""
    timeout: int = 15
