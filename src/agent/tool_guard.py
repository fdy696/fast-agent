"""工具调用守护 — 统一的错误日志 + 飞书告警."""

import asyncio
from log import logger


async def _alert(title: str, content: str) -> None:
    from utils.queue import queue
    await queue.enqueue("send_feishu_alert", title=title, content=content)


def make_guarded(name: str, func):
    """返回包装后的协程函数，失败时日志 + 飞书告警 + re-raise."""

    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"工具调用失败 [{name}]: {e}")
            asyncio.create_task(_alert("工具调用失败", f"工具: {name}\n错误: {e}"))
            raise

    wrapper.__name__ = name
    return wrapper
