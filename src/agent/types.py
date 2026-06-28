"""Runtime-only agent types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: Any | None = None
    error: str | None = None


@dataclass(slots=True)
class AgentLoopRequest:
    prompt: str
    system_prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    tools: list[ToolDefinition] = field(default_factory=list)


@dataclass(slots=True)
class AgentEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRunResult:
    answer: str
    model: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
