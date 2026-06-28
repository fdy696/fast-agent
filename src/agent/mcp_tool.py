import asyncio
from typing import Any, Dict, List, cast

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from core.config import settings
from log import logger

MCP_SERVERS_CONFIG = {
    "WebSearch": {
        "url": settings.MCP_WEB_SEARCH_URL,
        "headers": {"Authorization": f"Bearer {settings.API_KEY}"},
    },
    "amap-maps": {
        "url": settings.MCP_AMAP_MAPS_URL,
        "headers": {"Authorization": f"Bearer {settings.AMAP_API_KEY}"},
    },
    "geng-weather": {
        "url": settings.MCP_WEATHER_URL,
        "headers": {"Authorization": f"Bearer {settings.APPCODE}"},
    },
    "geng-search-image": {
        "url": settings.MCP_SEARCH_IMAGE_URL,
        "headers": {"Authorization": f"Bearer {settings.APPCODE}"},
    },
    "geng-map-crawler": {
        "url": settings.MCP_MAP_CRAWLER_URL,
        "headers": {"Authorization": f"Bearer {settings.APPCODE}"},
    },
}

mcp_clients: Dict[str, Any] = {}
openai_tools_pool: List[Dict[str, Any]] = []
tool_to_server_map: Dict[str, str] = {}


async def _connect_single_server(name: str, config: dict[str, Any]):
    url = cast(str, config["url"])
    headers = cast(dict[str, str], config.get("headers", {}))
    try:
        transport = StreamableHttpTransport(url=url, headers=headers)
        client = Client(transport=transport)
        await client.__aenter__()
        mcp_clients[name] = client

        tools = await client.list_tools()
        for tool in tools:
            tool_to_server_map[tool.name] = name
            openai_tool_schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            openai_tools_pool.append(openai_tool_schema)
    except Exception as e:
        logger.error(f"MCP connection failed for {name}: {str(e)}")


async def init_mcp_servers():
    """通过 asyncio.gather 并发初始化所有 MCP 服务"""
    logger.info("MCP: starting concurrent init for all remote servers...")

    tasks = [
        _connect_single_server(name, config)
        for name, config in MCP_SERVERS_CONFIG.items()
    ]
    await asyncio.gather(*tasks)

    from agent.tools import local_tools
    from agent.tools.date_tool import get_current_date
    from agent.tools.skill_loader import load_skill

    local_tools["get_current_date"]["func"] = get_current_date
    local_tools["load_skill"]["func"] = load_skill
    logger.info("MCP: all servers initialized")


async def close_mcp_servers():
    """安全断开所有 MCP 连接"""
    tasks = [client.__aexit__(None, None, None) for client in mcp_clients.values()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("MCP: all servers disconnected")


async def call_mcp_tool(
    server_name: str, tool_name: str, arguments: Dict[str, Any]
) -> Any:
    """业务层直接调用此函数请求 MCP 服务"""
    if server_name not in mcp_clients:
        raise ValueError(f"未找到激活的 MCP 服务: '{server_name}'")

    client = mcp_clients[server_name]
    result = await client.call_tool(tool_name, arguments)

    if getattr(result, "structured_content", None) is not None:
        return result.structured_content

    if getattr(result, "content", None):
        text_list = [item.text for item in result.content if hasattr(item, "text")]
        if text_list:
            return text_list[0] if len(text_list) == 1 else text_list

    return str(result)
