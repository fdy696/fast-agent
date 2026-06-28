"""执行工具调用：本地工具 → MCP 服务器分发"""

import asyncio
import json
from typing import Any, List

from openai.types.chat import ChatCompletionMessageToolCallParam

from agent.mcp_tool import call_mcp_tool, tool_to_server_map
from agent.tools import local_tools
from core.config import settings


async def execute_tool_calls(
    tool_calls: List[ChatCompletionMessageToolCallParam],
) -> List[dict[str, Any]]:
    tool_messages: List[dict[str, Any]] = []

    for tc in tool_calls:
        tool_name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
        except Exception:
            args = {}

        if tool_name in local_tools and local_tools[tool_name]["func"] is not None:
            try:
                result = await local_tools[tool_name]["func"](**args)
            except Exception as e:
                result = {"error": f"本地工具执行失败: {str(e)}"}
        elif tool_name in tool_to_server_map:
            server_name = tool_to_server_map[tool_name]
            try:
                result = await asyncio.wait_for(
                    call_mcp_tool(server_name, tool_name, args),
                    timeout=settings.MCP_TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                result = {"error": f"工具调用超时 ({settings.MCP_TOOL_TIMEOUT}s)"}
            except Exception as e:
                result = {"error": f"MCP 工具执行失败: {str(e)}"}
        else:
            result = {"error": f"未找到工具: {tool_name}"}

        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False),
        })

    return tool_messages
