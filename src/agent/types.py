"""Runtime-only agent types."""

from __future__ import annotations

# This module previously contained AgentLoopRequest, AgentEvent, and AgentRunResult
# dataclasses that were exclusively used by the now-removed AgentLoop.
# The types are no longer needed — pydantic-ai's native AgentRunResult,
# StreamedRunResult, and AGUIAdapter types are used directly instead.
