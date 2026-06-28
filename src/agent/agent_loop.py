"""Pure PydanticAI runtime loop.

No database access, no HTTP/SSE formatting, no business lifecycle handling.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .model_client import ModelClient, create_model_client
from .tools import ToolAdapter, ToolExecutor, create_default_tool_executor
from .types import AgentEvent, AgentLoopRequest, AgentRunResult


class AgentLoop:
    def __init__(
        self,
        model_client: ModelClient | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.model_client = model_client or create_model_client()
        self.tool_executor = tool_executor or create_default_tool_executor()

    async def stream(self, request: AgentLoopRequest) -> AsyncIterator[AgentEvent]:
        if not request.prompt.strip():
            yield AgentEvent("error", {"message": "prompt must not be empty"})
            return

        pending_tool_events: list[AgentEvent] = []

        def collect_tool_event(event: AgentEvent) -> None:
            pending_tool_events.append(event)

        try:
            pydantic_tools = ToolAdapter.to_pydantic_tools(
                self.tool_executor,
                request.tools,
                event_sink=collect_tool_event,
            )
            agent = self.model_client.build_agent(
                system_prompt=request.system_prompt,
                tools=pydantic_tools,
            )

            async with agent.run_stream(request.prompt) as result:
                async for text in result.stream_text(delta=True):
                    while pending_tool_events:
                        yield pending_tool_events.pop(0)
                    if text:
                        yield AgentEvent("llm.delta", {"text": text})

            while pending_tool_events:
                yield pending_tool_events.pop(0)
            yield AgentEvent("done", {})
        except Exception as exc:
            yield AgentEvent("error", {"message": str(exc)})

    async def run(self, request: AgentLoopRequest) -> AgentRunResult:
        chunks: list[str] = []
        async for event in self.stream(request):
            if event.type == "llm.delta":
                chunks.append(str(event.data.get("text", "")))
            elif event.type == "error":
                chunks.append(str(event.data.get("message", "")))
                break
        return AgentRunResult(
            answer="".join(chunks),
            tool_calls=self.tool_executor.history,
        )
