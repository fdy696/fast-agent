"""工具注册中心 — local_tools 定义 + MCP 工具合并"""

from typing import Any, Dict

from agent.mcp_tool import openai_tools_pool, tool_to_server_map

# 本地工具注册表：key=工具名，value={func, schema}
# func 在 init_mcp_servers() 中延迟绑定
local_tools: Dict[str, dict[str, Any]] = {
    "get_current_date": {
        "func": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "get_current_date",
                "description": "获取当前系统日期。注意：此日期仅供后台逻辑参考，绝对禁止用于出行日期、绝对禁止据此推算用户出发时间。出行日期只能由用户明确告知。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    },
    "load_skill": {
        "func": None,
        "schema": {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "加载指定能力的完整工作流，返回该 skill 的详细指令与操作流程。仅在判断当前任务需要该能力时调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "要加载的能力名称，如 travel-planner",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
    },
}


def get_all_tool_schemas() -> list[dict[str, Any]]:
    """返回 MCP 工具 + 本地工具的全部 OpenAI 兼容 schema"""
    local_schemas = [t["schema"] for t in local_tools.values()]
    return openai_tools_pool + local_schemas
